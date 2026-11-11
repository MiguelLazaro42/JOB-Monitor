#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ApplicationServices/ApplicationServices.h>
#import <AVFoundation/AVFoundation.h>
#include <dlfcn.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

extern char **environ;
typedef int (*Py_BytesMain_t)(int, char **);

static NSString *default_install_dir(void) {
    NSString *home = NSHomeDirectory();
    return [home stringByAppendingPathComponent:@".local/share/MonitoreoEmpresa"];
}

static NSDictionary *load_runtime_paths(NSString *installDir) {
    NSString *cfgPath = [installDir stringByAppendingPathComponent:@"runtime_paths.json"];
    NSData *data = [NSData dataWithContentsOfFile:cfgPath];
    if (!data) return @{};
    id obj = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    return [obj isKindOfClass:[NSDictionary class]] ? obj : @{};
}

static BOOL check_full_disk_access(void) {
    NSArray<NSString *> *dirs = @[
        [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Safari"],
        [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Messages"]
    ];
    NSFileManager *fm = [NSFileManager defaultManager];
    int tested = 0;
    for (NSString *d in dirs) {
        BOOL isDir = NO;
        if ([fm fileExistsAtPath:d isDirectory:&isDir] && isDir) {
            tested++;
            errno = 0;
            DIR *dp = opendir(d.UTF8String);
            if (dp) {
                closedir(dp);
                return YES;
            }
            if (errno == EPERM || errno == EACCES) {
                return NO;
            }
        }
    }
    const char *tmPlist = "/Library/Preferences/com.apple.TimeMachine.plist";
    if (access(tmPlist, F_OK) == 0) {
        tested++;
        errno = 0;
        int fd = open(tmPlist, O_RDONLY);
        if (fd >= 0) {
            close(fd);
            return YES;
        }
        if (errno == EPERM || errno == EACCES) {
            return NO;
        }
    }
    return tested == 0;
}

static void write_status_json(NSString *statusPath, BOOL screenOk, BOOL axOk, BOOL micOk, BOOL fdaOk) {
    NSDictionary *dict = @{
        @"screen": @(screenOk),
        @"accessibility": @(axOk),
        @"microphone": @(micOk),
        @"full_disk": @(fdaOk)
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:dict options:NSJSONWritingPrettyPrinted error:nil];
    if (data) {
        [data writeToFile:statusPath atomically:YES];
    }
}

static void trigger_screen_tcc_registration(void) {
    if (!CGPreflightScreenCaptureAccess()) {
        CGRequestScreenCaptureAccess();
        CGImageRef img = CGDisplayCreateImage(CGMainDisplayID());
        if (img) {
            CGImageRelease(img);
        }
    }
}

int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *installDir = default_install_dir();
        NSString *statusPath = [installDir stringByAppendingPathComponent:@"permissions_status.json"];

        for (int i = 1; i < argc; i++) {
            if (strcmp(argv[i], "--check-screen-only") == 0) {
                return CGPreflightScreenCaptureAccess() ? 0 : 2;
            }
            if (strcmp(argv[i], "--check-permissions") == 0) {
                BOOL screenOk = CGPreflightScreenCaptureAccess();
                BOOL axOk = AXIsProcessTrusted();
                BOOL fdaOk = check_full_disk_access();
                AVAuthorizationStatus micSt = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
                write_status_json(statusPath, screenOk, axOk, micSt == AVAuthorizationStatusAuthorized, fdaOk);
                return 0;
            }
            if (strcmp(argv[i], "--request-permissions") == 0) {
                [NSApplication sharedApplication];
                [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];

                BOOL screenOk = CGPreflightScreenCaptureAccess();
                if (!screenOk) {
                    trigger_screen_tcc_registration();
                }

                BOOL axOk = AXIsProcessTrusted();
                if (!axOk) {
                    NSDictionary *axOpts = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
                    axOk = AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)axOpts);
                }

                BOOL fdaOk = check_full_disk_access();

                AVAuthorizationStatus micSt = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
                __block BOOL micOk = (micSt == AVAuthorizationStatusAuthorized);
                if (micSt == AVAuthorizationStatusNotDetermined) {
                    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL granted) {
                        micOk = granted;
                    }];
                }

                // Mantener vivo el RunLoop 2.5s para que tccd y WindowServer registren la app en Ajustes del Sistema
                [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:2.5]];

                screenOk = CGPreflightScreenCaptureAccess();
                axOk = AXIsProcessTrusted();
                fdaOk = check_full_disk_access();
                micSt = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
                micOk = (micSt == AVAuthorizationStatusAuthorized);
                write_status_json(statusPath, screenOk, axOk, micOk, fdaOk);
                return 0;
            }
        }

        NSDictionary *rt = load_runtime_paths(installDir);
        NSString *cfgInstallDir = rt[@"install_dir"];
        if ([cfgInstallDir isKindOfClass:[NSString class]] && cfgInstallDir.length > 0) {
            installDir = cfgInstallDir;
        }

        chdir(installDir.UTF8String);
        setenv("PYTHONUNBUFFERED", "1", 1);

        NSString *pyHome = rt[@"python_home"];
        NSString *pyDylib = rt[@"python_dylib"];
        NSString *sitePkg = rt[@"site_packages"];
        NSString *pyBin = rt[@"python_bin"];
        NSString *agentScript = [installDir stringByAppendingPathComponent:@"agent.py"];

        // Asegurar registro en TCC de Grabacion de Pantalla tambien desde el proceso demonio principal
        if (!CGPreflightScreenCaptureAccess()) {
            CGImageRef testImg = CGDisplayCreateImage(CGMainDisplayID());
            if (testImg) {
                CGImageRelease(testImg);
            }
        }

        if ([pyHome isKindOfClass:[NSString class]] && pyHome.length > 0) {
            setenv("PYTHONHOME", pyHome.UTF8String, 1);
        }
        if ([sitePkg isKindOfClass:[NSString class]] && sitePkg.length > 0) {
            setenv("PYTHONPATH", sitePkg.UTF8String, 1);
        }

        if ([pyDylib isKindOfClass:[NSString class]] && pyDylib.length > 0) {
            void *handle = dlopen(pyDylib.UTF8String, RTLD_LAZY | RTLD_GLOBAL);
            if (handle) {
                Py_BytesMain_t py_main = (Py_BytesMain_t)dlsym(handle, "Py_BytesMain");
                if (py_main) {
                    char *py_argv[64];
                    int n = 0;
                    py_argv[n++] = argv[0];
                    py_argv[n++] = strdup(agentScript.UTF8String);
                    for (int i = 1; i < argc && n < 62; i++) {
                        py_argv[n++] = argv[i];
                    }
                    py_argv[n] = NULL;
                    return py_main(n, py_argv);
                }
            }
        }

        // Respaldo: lanzar python como subproceso hijo manteniendo JOBMonitorAgent vivo como proceso responsable TCC
        if (![pyBin isKindOfClass:[NSString class]] || pyBin.length == 0) {
            pyBin = [installDir stringByAppendingPathComponent:@".venv/bin/python"];
        }
        unsetenv("PYTHONHOME");
        char *child_argv[64];
        int cn = 0;
        child_argv[cn++] = strdup(pyBin.UTF8String);
        child_argv[cn++] = strdup(agentScript.UTF8String);
        for (int i = 1; i < argc && cn < 62; i++) {
            child_argv[cn++] = argv[i];
        }
        child_argv[cn] = NULL;

        pid_t pid = 0;
        if (posix_spawn(&pid, pyBin.UTF8String, NULL, NULL, child_argv, environ) == 0) {
            int status = 0;
            waitpid(pid, &status, 0);
            if (WIFEXITED(status)) {
                return WEXITSTATUS(status);
            }
            return 1;
        }

        fprintf(stderr, "[JOBMonitorAgent] No se pudo iniciar el runtime de Python en %s\n", pyBin.UTF8String);
        return 1;
    }
}

