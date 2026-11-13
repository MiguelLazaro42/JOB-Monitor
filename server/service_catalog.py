"""
Catálogo público de reconocimiento de servicios por DNS/IP para JOB-Monitor.

Fuentes: AdGuard HostlistsRegistry (assets/services.json, 142 servicios) + servicios curados
empresariales/LatAm/productividad + bloques IPv4 estilo nDPI para tráfico DoH (solo IP visible).

IMPORTANTE: el bloque desde `_SERVICE_RULES` hasta el final de `match_service_domain_or_ip` es IDÉNTICO
al embebido en agent/agent.py (el agente se despliega como archivo único). Mantenerlos sincronizados.
"""
from __future__ import annotations

_SERVICE_RULES = {
    'youtube.com': ('STREAMING',
        'ggpht.cn ggpht.com googlevideo.com wide-youtube.l.google.com withyoutube.com youtu.be youtube- '
        'nocookie.com youtube-ui.l.google.com youtube.ae youtube.al youtube.am youtube.at youtube.az '
        'youtube.ba youtube.be youtube.bg youtube.bh youtube.bo youtube.by youtube.ca youtube.cat '
        'youtube.ch youtube.cl youtube.co youtube.co.ae youtube.co.at youtube.co.cr youtube.co.hu '
        'youtube.co.id youtube.co.il youtube.co.in youtube.co.jp youtube.co.ke youtube.co.kr '
        'youtube.co.ma youtube.co.nz youtube.co.th youtube.co.tz youtube.co.ug youtube.co.uk '
        'youtube.co.ve youtube.co.za youtube.co.zw youtube.com youtube.com.ar youtube.com.au '
        'youtube.com.az youtube.com.bd youtube.com.bh youtube.com.bo youtube.com.br youtube.com.by '
        'youtube.com.co youtube.com.do youtube.com.ec youtube.com.ee youtube.com.eg youtube.com.es '
        'youtube.com.gh youtube.com.gr youtube.com.gt youtube.com.hk youtube.com.hn youtube.com.hr '
        'youtube.com.jm youtube.com.jo youtube.com.kw youtube.com.lb youtube.com.lv youtube.com.ly '
        'youtube.com.mk youtube.com.mt youtube.com.mx youtube.com.my youtube.com.ng youtube.com.ni '
        'youtube.com.om youtube.com.pa youtube.com.pe youtube.com.ph youtube.com.pk youtube.com.pt '
        'youtube.com.py youtube.com.qa youtube.com.ro youtube.com.sa youtube.com.sg youtube.com.sv '
        'youtube.com.tn youtube.com.tr youtube.com.tw youtube.com.ua youtube.com.uy youtube.com.ve '
        'youtube.cr youtube.cz youtube.de youtube.dk youtube.ee youtube.es youtube.fi youtube.fr '
        'youtube.ge youtube.googleapis.com youtube.gr youtube.gt youtube.hk youtube.hr youtube.hu '
        'youtube.ie youtube.in youtube.iq youtube.is youtube.it youtube.jo youtube.jp youtube.kr '
        'youtube.kz youtube.la youtube.lk youtube.lt youtube.lu youtube.lv youtube.ly youtube.ma '
        'youtube.md youtube.me youtube.mk youtube.mn youtube.mx youtube.my youtube.ng youtube.ni '
        'youtube.nl youtube.no youtube.pa youtube.pe youtube.ph youtube.pk youtube.pl youtube.pr '
        'youtube.pt youtube.qa youtube.ro youtube.rs youtube.ru youtube.sa youtube.se youtube.sg '
        'youtube.si youtube.sk youtube.sn youtube.soy youtube.sv youtube.tn youtube.tv youtube.ua '
        'youtube.ug youtube.uy youtube.vn youtubeeducation.com youtubeembeddedplayer.googleapis.com '
        'youtubefanfest.com youtubegaming.com youtubego.co.id youtubego.co.in youtubego.com '
        'youtubego.com.br youtubego.id youtubego.in youtubei.googleapis.com youtubekids.com '
        'youtubemobilesupport.com yt.be yt3.ggpht.com yt3.googleusercontent.com ytimg.com'),
    'netflix.com': ('STREAMING',
        'fast.com netflix.ca netflix.com netflix.com.edgesuite.net netflix.net netflixdnstest1.com '
        'netflixdnstest10.com netflixdnstest2.com netflixdnstest3.com netflixdnstest4.com '
        'netflixdnstest5.com netflixdnstest6.com netflixdnstest7.com netflixdnstest8.com '
        'netflixdnstest9.com netflixinvestor.com netflixtechblog.com nflxext.com nflximg.com nflximg.net '
        'nflxsearch.net nflxso.net nflxvideo.net'),
    'tiktok.com': ('SOCIAL',
        'amemv.com bdurl.com bytecdn.cn bytedance.map.fastly.net bytedapm.com bytegoofy.com byteimg.com '
        'byteoversea.com bytescm.com douyin.com douyincdn.com douyinliving.com douyinpic.com '
        'douyinstatic.com douyinvod.com huoshan.com huoshanstatic.com huoshanzhibo.com ibytedtos.com '
        'ibyteimg.com muscdn.com musical.ly p16-tiktokcdn-com.akamaized.net pstatp.com snssdk.com '
        'tiktok.com tiktok.tps.dv.tech tiktokcdn-eu.com tiktokcdn-eu.com.ttdns3.com tiktokcdn-us.com '
        'tiktokcdn.com tiktokrow-cdn.com tiktokv.com tiktokv.eu ttlivecdn.com ttlivecdn.com.c.bytefcdn- '
        'oversea.com ttwstatic.com zijieapi.com'),
    'whatsapp.com': ('MESSAGING',
        'wa.me whatsapp-plus.info whatsapp-plus.me whatsapp-plus.net whatsapp.cc whatsapp.com '
        'whatsapp.info whatsapp.net whatsapp.org whatsapp.tv whatsappbrand.com'),
    'facebook.com': ('SOCIAL',
        'aboutfacebook.com accessfacebookfromschool.com accountkit.com acebooik.com acebook.com '
        'advancediddetection.com askfacebook.net askfacebook.org atdmt2.com atlasdmt.com '
        'atlasonepoint.com atscaleconference.com botorch.org buck.build buckbuild.com '
        'buyingfacebooklikes.com careersatfb.com celebgramme.com china-facebook.com click-url.com como- '
        'hackearfacebook.com componentkit.org crowdtangle.com dacebook.com dlfacebook.com dotfacebook.com '
        'dotfacebook.net draftjs.org expresswifi.com f8.com faacebok.com faacebook.com faasbook.com '
        'facbebook.com facbeok.com facboo.com facbook.com facbool.com facboox.com faccebook.com '
        'faccebookk.com facdbook.com facdebook.com face-book.com faceabook.com facebboc.com facebbook.com '
        'facebboook.com facebcook.com facebdok.com facebgook.com facebhook.com facebkkk.com facebo-ok.com '
        'faceboak.com facebock.com facebocke.com facebof.com faceboik.com facebok.com facebokbook.com '
        'facebokc.com facebokk.com facebokok.com faceboks.com facebol.com facebolk.com facebomok.com '
        'faceboo.com facebooa.com faceboob.com faceboobok.com facebooc.com faceboock.com facebood.com '
        'facebooe.com faceboof.com facebooi.com facebooik.com facebooik.org facebooj.com facebook- '
        'corp.com facebook-covid-19.com facebook-ebook.com facebook-forum.com facebook-hardware.com '
        'facebook-inc.com facebook-login.com facebook-newsroom.com facebook-newsroom.org facebook- '
        'pmdcenter.com facebook-pmdcenter.net facebook-pmdcenter.org facebook-privacy.com facebook- '
        'program.com facebook-studio.com facebook-support.org facebook-texas-holdem.com facebook-texas- '
        'holdem.net facebook.br facebook.ca facebook.cc facebook.com facebook.design facebook.hu '
        'facebook.in facebook.net facebook.nl facebook.org facebook.se facebook.shop facebook.tv '
        'facebook.us facebook.wang facebook123.org facebook30.com facebook30.net facebook30.org '
        'facebook4business.com facebookads.com facebookadvertisingsecrets.com facebookappcenter.info '
        'facebookappcenter.net facebookappcenter.org facebookatschool.com facebookawards.com '
        'facebookblueprint.net facebookbrand.com facebookbrand.net '
        'facebookcanadianelectionintegrityinitiative.com facebookcareer.com facebookcheats.com '
        'facebookck.com facebookclub.com facebookcom.com facebookconnect.com facebookconsultant.org '
        'facebookcoronavirus.com facebookcovers.org facebookcredits.info facebookdating.net '
        'facebookdevelopergarage.com facebookdusexe.org facebookemail.com facebookenespanol.com '
        'facebookexchange.com facebookexchange.net facebookfacebook.com facebookflow.com '
        'facebookgames.com facebookgraphsearch.com facebookgraphsearch.info facebookgroups.com '
        'facebookhome.cc facebookhome.com facebookhome.info facebookhub.com facebooki.com facebookinc.com '
        'facebookland.com facebooklikeexchange.com facebooklive.com facebooklivestaging.net '
        'facebooklivestaging.org facebooklogin.com facebooklogin.info facebookloginhelp.net '
        'facebooklogs.com facebookmail.com facebookmail.tv facebookmanager.info facebookmarketing.info '
        'facebookmarketingpartner.com facebookmarketingpartners.com facebookmobile.com facebookmsn.com '
        'facebooknews.com facebooknfl.com facebooknude.com facebookofsex.com facebookook.com '
        'facebookpaper.com facebookpay.com facebookphonenumber.net facebookphoto.com facebookphotos.com '
        'facebookpmdcenter.com facebookpoke.net facebookpoke.org facebookpoker.info '
        'facebookpokerchips.info facebookporn.net facebookporn.org facebookporno.net facebookportal.com '
        'facebooks.com facebooksafety.com facebooksecurity.net facebookshop.com facebooksignup.net '
        'facebooksite.net facebookstories.com facebookstudios.net facebookstudios.org '
        'facebooksupplier.com facebooksuppliers.com facebookswagemea.com facebookswagstore.com '
        'facebooksz.com facebookthreads.net facebooktv.net facebooktv.org facebookvacation.com '
        'facebookw.com facebookwork.com facebookworld.com facebool.com facebool.info facebooll.com '
        'faceboom.com faceboon.com faceboonk.com faceboooik.com faceboook.com faceboop.com faceboot.com '
        'faceboox.com facebopk.com facebpook.com facebuk.com facebuok.com facebvook.com facebyook.com '
        'facebzook.com facecbgook.com facecbook.com facecbook.org facecook.com facecook.org facedbook.com '
        'faceebok.com faceebook.com faceebot.com facegbok.com facegbook.com faceobk.com faceobok.com '
        'faceobook.com faceook.com facerbooik.com facerbook.com facesbooc.com facesounds.com facetook.com '
        'facevbook.com facewbook.co facewook.com facfacebook.com facfebook.com faciometrics.com '
        'fackebook.com facnbook.com facrbook.com facvebook.com facwebook.com facxebook.com fadebook.com '
        'faebok.com faebook.com faebookc.com faeboook.com faecebok.com faesebook.com fafacebook.com '
        'faicbooc.com fasebokk.com fasebook.com faseboox.com fasttext.cc favebook.com faycbok.com '
        'fb.careers fb.com fb.gg fb.me fb.watch fbacebook.com fbbmarket.com fbboostyourbusiness.com '
        'fbcdn-a.akamaihd.net fbcdn.com fbcdn.net fbf8.com fbfeedback.com fbhome.com fbidb.io fbinc.com '
        'fbinfer.com fbinnovation.com fblitho.com fbmarketing.com fbmessenger.com fbpigeon.com '
        'fbredex.com fbreg.com fbrell.com fbrpms.com fbsbx.com fbsbx.net fbsupport-covid.net '
        'fbthirdpartypixel.com fbthirdpartypixel.net fbthirdpartypixel.org fburl.com fbwat.ch '
        'fbworkmail.com fcacebook.com fcaebook.com fcebook.com fcebookk.com fcfacebook.com fdacebook.info '
        'feacboo.com feacbook.com feacbooke.com feacebook.com fecbbok.com fecbooc.com fecbook.com '
        'feceboock.com fecebook.net feceboox.com fececbook.com feook.com ferabook.com fescebook.com '
        'fesebook.com ffacebook.com fgacebook.com ficeboock.com flow.dev flow.org flowtype.org '
        'fmcebook.com fnacebook.com fosebook.com fpacebook.com fqcebook.com fracebook.com freeb.com '
        'freebasics.com freebasics.net freebs.com freefacebook.com freefacebook.net freefacebookads.net '
        'freefblikes.com freindfeed.com frescolib.org friendbook.info friendfed.com friendfeed-api.com '
        'friendfeed-media.com friendfeed.com friendfeedmedia.com fsacebok.com fscebook.com '
        'fundraisingwithfacebook.com funnyfacebook.org futureofbusinesssurvey.org gacebook.com '
        'gameroom.com gfacecbook.com groups.com hackerfacebook.com hackfacebook.com hackfacebookid.com '
        'hacklang.org hhvm.com hifacebook.info howtohackfacebook-account.com hsfacebook.com '
        'httpfacebook.com httpsfacebook.com httpwwwfacebook.com i.org internet.org klik.me liverail.com '
        'liverail.tv login-account.net makeitopen.com markzuckerberg.com mcrouter.net mcrouter.org '
        'messengerdevelopers.com midentsolutions.com mobilefacebook.com moneywithfacebook.com '
        'myfbfans.com nbabot.net newsfeed.com nextstop.com ogp.me online-deals.net opencreate.org '
        'opengraphprotocol.com opengraphprotocol.org parse.com pyrobot.org reachtheworldonfacebook.com '
        'react.com reactjs.com reactjs.org recoiljs.org redkix.com rocksdb.com rocksdb.net rocksdb.org '
        'shopfacebook.com sportsfacebook.com sportstream.com supportfacebook.com terragraph.com '
        'thefacebook.com thefacebook.net thefind.com toplayerserver.com viewpointsfromfacebook.com '
        'whyfacebook.com workplace.com workplaceusecases.com worldhack.com www-facebook.com '
        'wwwfacebok.com wwwfacebook.com wwwmfacebook.com yogalayout.com zuckerberg.com zuckerberg.net'),
    'instagram.com': ('SOCIAL',
        'achat-followers-instagram.com acheter-followers-instagram.com acheterdesfollowersinstagram.com '
        'acheterfollowersinstagram.com bookstagram.com carstagram.com cdninstagram.com chickstagram.com '
        'ig.me igcdn.com igsonar.com igtv.com imstagram.com imtagram.com instaadder.com instachecker.com '
        'instafallow.com instafollower.com instagainer.com instagda.com instagify.com instagmania.com '
        'instagor.com instagr.am instagram-brand.com instagram-engineering.com instagram-help.com '
        'instagram-press.com instagram-press.net instagram.com instagramci.com instagramcn.com '
        'instagramdi.com instagramhashtags.net instagramhilecim.com instagramhilesi.org instagramium.com '
        'instagramizlenme.com instagramkusu.com instagramlogin.com instagramm.com instagramn.com '
        'instagrampartners.com instagramphoto.com instagramq.com instagramsepeti.com '
        'instagramtakipcisatinal.net instagramtakiphilesi.com instagramtips.com instagramtr.com '
        'instagran.com instagranm.com instagrem.com instagrm.com instagtram.com instagy.com '
        'instamgram.com instangram.com instanttelegram.com instaplayer.net instastyle.tv instgram.com '
        'intagram.com intagrm.com intgram.com kingstagram.com lnstagram-help.com oninstagram.com online- '
        'instagram.com onlineinstagram.com theinstagramhack.com web-instagram.net wwwinstagram.com'),
    'threads.net': ('SOCIAL',
        'threads.com threads.net'),
    'messenger.com': ('MESSAGING',
        'm.me messenger.com'),
    'telegram.org': ('MESSAGING',
        'cdn-telegram.org comments.app contest.com graph.org quiz.directory t.me tdesktop.com telega.one '
        'telegra.ph telegram-cdn.org telegram.dog telegram.me telegram.org telegram.space telesco.pe '
        'tg.dev tx.me usercontent.dev'),
    'discord.com': ('MESSAGING',
        'airhorn.solutions airhornbot.com bigbeans.solutions dis.gd discord-activities.com discord.co '
        'discord.com discord.design discord.dev discord.gg discord.gift discord.gifts discord.media '
        'discord.new discord.store discord.tools discordactivities.com discordapp.com discordapp.io '
        'discordapp.net discordcdn.com discordmerch.com discordpartygames.com discordsays.com '
        'discordstatic.com discordstatus.com hammerandchisel.ssl.zendesk.com watchanimeattheoffice.com'),
    'spotify.com': ('STREAMING',
        'audio-ak-spotify-com.akamaized.net audio4-ak-spotify-com.akamaized.net byspotify.com eip- '
        'ntt.video-ak.cdn.spotify.com.akahost.net heads-ak-spotify-com.akamaized.net heads4-ak-spotify- '
        'com.akamaized.net pscdn.co scdn.co spoti.fi spotify-everywhere.com spotify.com '
        'spotify.com.edgesuite.net spotify.design spotify.map.fastly.net spotify.map.fastlylb.net '
        'spotifycdn.com spotifycdn.net spotifycharts.com spotifycodes.com spotifyforbrands.com '
        'spotifyjobs.com spotilocal.com video-ak.cdn.spotify.com video-akpcw-cdn-spotify- '
        'com.akamaized.net video-akpcw.spotifycdn.com video-akpcw.spotifycdn.com.edgesuite.net video- '
        'fa.scdn.co'),
    'twitch.tv': ('STREAMING',
        'ext-twitch.tv jtvnw.net ttvnw.net twitch.tv twitchcdn.net twitchsvc.net'),
    'kick.com': ('STREAMING',
        'kick.com'),
    'disneyplus.com': ('STREAMING',
        'bamgrid.com disney-plus.net disney.playback.edge.bamgrid.com disneynow.com disneyplus.com '
        'disneystreaming.com dssott.com hotstar.com media.dssott.com star.playback.edge.bamgrid.com '
        'starplus.com'),
    'max.com': ('STREAMING',
        'hbo.com hbogo.co.th hbogo.com hbogo.eu hbogoasia.com hbogoasia.id hbogoasia.ph hbomax- '
        'images.warnermediacdn.com hbomax.com hbomaxcdn.com hbonow.com max.com maxgo.com'),
    'primevideo.com': ('STREAMING',
        'aiv-cdn.net aiv-delivery.net amazonmusic.com amazonprimevideo.cn amazonprimevideo.com.cn '
        'amazonprimevideos.com amazonvideo.cc amazonvideo.com amazonvideodirect.com atv-ext-eu.amazon.com '
        'atv-ext-fe.amazon.com atv-ext.amazon.com atv-ps-eu.amazon.co.uk atv-ps-eu.amazon.com atv-ps- '
        'fe.amazon.co.jp atv-ps-fe.amazon.com atv-ps.amazon.com av-eu.amazon.com av-na.amazon.com '
        'music.a2z.com music.amazon.co.uk music.amazon.com music.amazon.in prime-video.com primevideo.cc '
        'primevideo.com primevideo.info primevideo.org primevideo.tv pv-cdn.net video.a2z.com'),
    'tv.apple.com': ('STREAMING',
        'applemusic.apple atv.apple.com hls-svod-aoc-ve.itunes.g.aaplimg.com itun.es itunes.apple.com '
        'itunes.ca itunes.co itunes.co.th itunes.com itunes.es itunes.g.aaplimg.com itunes.hk itunes.mx '
        'itunes.org itunes.us music.apple.com tv.apple.com tv.g.apple.com tv.v.aaplimg.com'),
    'chatgpt.com': ('AI',
        'chatgpt.com oaistatic.com oaiusercontent.com openai.com'),
    'claude.ai': ('AI',
        'anthropic.com claude.ai claude.com claudeusercontent.com'),
    'gemini.google.com': ('AI',
        'aistudio.google.com bard.google.com gemini.google.com generativelanguage.googleapis.com'),
    'copilot.microsoft.com': ('AI',
        'copilot.ai copilot.cloud.microsoft copilot.com copilot.microsoft.com copilotstudio.microsoft.com'),
    'perplexity.ai': ('AI',
        'perplexity.ai pplx.ai'),
    'deepseek.com': ('AI',
        'deepseek.com'),
    'canva.com': ('WORK',
        'canva.com'),
    'figma.com': ('WORK',
        'figma.com'),
    'notion.so': ('WORK',
        'notion-static.com notion.com notion.so'),
    'slack.com': ('WORK',
        'slack-edge.com slack-imgs.com slack-msgs.com slack.com slackb.com'),
    'zoom.us': ('WORK',
        'zoom.com zoom.us zoomgov.com'),
    'teams.microsoft.com': ('WORK',
        'teams.cdn.office.net teams.live.com teams.microsoft.com'),
    'meet.google.com': ('WORK',
        'meet.google.com'),
    'drive.google.com': ('CLOUD',
        'drive.google.com drive.usercontent.google.com'),
    'docs.google.com': ('WORK',
        'docs.google.com sheets.google.com slides.google.com'),
    'mail.google.com': ('WORK',
        'gmail.com mail.google.com'),
    'outlook.com': ('WORK',
        'outlook.com outlook.live.com outlook.office.com outlook.office365.com'),
    'office365.com': ('WORK',
        'microsoftonline.com office.com office.net office365.com'),
    'onedrive.live.com': ('CLOUD',
        '1drv.com 1drv.ms onedrive.com onedrive.live.com'),
    'sharepoint.com': ('CLOUD',
        'sharepoint.com svc.ms'),
    'dropbox.com': ('CLOUD',
        'addtodropbox.com app.hellosign.com dash.ai db.tt docsend.com dropbox-dns.com dropbox.com '
        'dropbox.tech dropbox.zendesk.com dropboxapi.com dropboxbusiness.com dropboxcaptcha.com '
        'dropboxforum.com dropboxforums.com dropboxinsiders.com dropboxlegal.com dropboxmail.com '
        'dropboxpartners.com dropboxstatic.com dropboxteam.com dropboxusercontent.com getdropbox.com'),
    'wetransfer.com': ('CLOUD',
        'wetransfer.com wetransfer.net'),
    'mega.nz': ('CLOUD',
        'mega.co.nz mega.io mega.nz'),
    'box.com': ('CLOUD',
        'box.com box.net boxcdn.net boxcloud.com'),
    'github.com': ('WORK',
        'github.com github.io githubassets.com githubusercontent.com'),
    'gitlab.com': ('WORK',
        'gitlab.com'),
    'bitbucket.org': ('WORK',
        'bbc.org bitbucket.org'),
    'stackoverflow.com': ('WORK',
        'sstatic.net stackexchange.com stackoverflow.com'),
    'atlassian.com': ('WORK',
        'atl-paas.net atlassian.com atlassian.net jira.com'),
    'trello.com': ('WORK',
        'trello.com'),
    'asana.com': ('WORK',
        'asana.com'),
    'monday.com': ('WORK',
        'monday.com'),
    'clickup.com': ('WORK',
        'clickup.com'),
    'linear.app': ('WORK',
        'linear.app'),
    'vercel.com': ('CLOUD',
        'vercel.app vercel.com'),
    'netlify.com': ('CLOUD',
        'netlify.app netlify.com'),
    'railway.app': ('CLOUD',
        'railway.app railway.com'),
    'digitalocean.com': ('CLOUD',
        'digitalocean.com digitaloceanspaces.com ondigitalocean.app'),
    'cloudflare.com': ('CLOUD',
        'argotunnel.com cf-ipfs.com cloudflare-dns.com cloudflare-ipfs.com cloudflare-quic.com '
        'cloudflare.com cloudflare.net cloudflare.tv cloudflareaccess.com cloudflareapps.com '
        'cloudflarebolt.com cloudflareclient.com cloudflareinsights.com cloudflareok.com '
        'cloudflarepreview.com cloudflareresolve.com cloudflaressl.com cloudflarestatus.com '
        'cloudflarestorage.com cloudflarestream.com cloudflaretest.com cloudflarewarp.com every1dns.net '
        'pacloudflare.com pages.dev trycloudflare.com videodelivery.net warp.plus workers.dev'),
    'aws.amazon.com': ('CLOUD',
        'amazonaws.com aws.amazon.com awsapps.com awsstatic.com'),
    'azure.com': ('CLOUD',
        'azure.com azure.net azureedge.net azurewebsites.net core.windows.net'),
    'cloud.google.com': ('CLOUD',
        'appspot.com cloud.google.com cloudfunctions.net firebaseapp.com run.app web.app'),
    'mercadolibre.com': ('SHOPPING',
        'mercadolibre.cl mercadolibre.co.cr mercadolibre.com mercadolibre.com.ar mercadolibre.com.bo '
        'mercadolibre.com.co mercadolibre.com.do mercadolibre.com.ec mercadolibre.com.gt '
        'mercadolibre.com.hn mercadolibre.com.mx mercadolibre.com.ni mercadolibre.com.pa '
        'mercadolibre.com.pe mercadolibre.com.py mercadolibre.com.sv mercadolibre.com.uy '
        'mercadolibre.com.ve mercadolivre.com.br mercadopago.com mercadopago.com.mx mlstatic.com'),
    'amazon.com': ('SHOPPING',
        'a2z.com a2z.org.cn aboutamazon.cn aboutamazon.co.uk aboutamazon.com aboutamazon.com.au '
        'aboutamazon.de aboutamazon.es aboutamazon.eu aboutamazon.fr aboutamazon.in aboutamazon.it '
        'aboutamazon.jp aboutamazon.pl acmvalidations.com acmvalidationsaws.com aesworkshops.com '
        'alexa.com alexafund.cn alexafund.com.cn amaaozn.com amazon-adsystem.com amazon-fashions.com '
        'amazon-jp-recruiting.com amazon-lantern.com amazon-launchpad.com amazon.ae amazon.ca amazon.cn '
        'amazon.co.jp amazon.co.uk amazon.com amazon.com.au amazon.com.be amazon.com.br amazon.com.mx '
        'amazon.com.tr amazon.de amazon.es amazon.fr amazon.in amazon.it amazon.jobs amazon.jp amazon.nl '
        'amazon.red amazon.se amazon.sg amazonalexavoxcon.com amazonauthorinsights.com amazonbusiness.cn '
        'amazonbusiness.com.cn amazonbusiness.org amazonbusinessblog.com amazonchoice.cn '
        'amazonchoice.com.cn amazonchoices.cn amazonchoices.com.cn amazondevicesupport.com '
        'amazonfctours.com amazonianblog.com amazonimages.com amazoninspire.cn amazoninspire.com.cn '
        'amazonlaunchpad.cn amazonlaunchpad.com amazonlaunchpad.com.cn amazonlending.com.cn '
        'amazonliterarypartnership.com amazonlumberyard.wang amazonnow.cn amazonnow.com.cn amazonpay.com '
        'amazonpay.in amazonsdi.com amazonses.com amazonstudiosguilds.com amazontrust.com '
        'amazonwebservices.com.cn amazonworkdocs.cn amazonworkdocs.com amazonworkdocs.com.cn '
        'amplifyapp.com amplifyframework.com amzn.asia amzn.com amzn.to amznl.com asfiovnxocqpcry.com.cn '
        'assoc-amazon.cn associates-amazon.com audible.com aws-border.cn aws-icp-domain-manager.cn aws- '
        'iot-hackathon.com awsapps.cn awsapps.com.cn awsautopilot.com awsautoscaling.com awsbraket.com '
        'awscommandlineinterface.com awsedstart.com awseducate.com awseducate.net awseducate.org '
        'awsglobalaccelerator.com awsloft-johannesburg.com awsloft-stockholm.com awssecworkshops.com '
        'awsstatic.cn awsthinkbox.com awstrack.me awstrust.com boxofficemojo.com cdkworkshop.com '
        'containersonaws.com createspace.com elasticbeanstalk.com gameon-masters.com gdansk-amazon.com '
        'images-amazon.com imdb.com imdb.to kindle.cn kindle.co.jp kindle.co.uk kindle.com kindle.de '
        'kindle.es kindle.fr kindle.in kindle.it kindle.jp kindleoasis.cn kindleoasis.com '
        'kindleoasis.com.cn kindleoasis.info kindleoasis.jp kindleoasis.org kindleoasis.us '
        'kindleoasisnews.com kindleproject.com media-amazon.com media-imdb.com nwcdcloud.cn '
        'nwcdcloud.com.cn nwcddns.cn nwcdinfosec.cn primeday.cn primeday.com.cn primeday.info route53.cn '
        'sagemaker.com.cn serving-sys.com siege-amazon.com ss2.us ssl-images-amazon.com '
        'thinkboxsoftware.com ueberamazon.de yamaxun.cn yamaxun.com z.cn'),
    'aliexpress.com': ('SHOPPING',
        'ae-rus.net ae-rus.ru alicdn.com aliexpress.com aliexpress.ru aliexpress.us'),
    'temu.com': ('SHOPPING',
        'kwcdn.com temu.com temucdn.com'),
    'shein.com': ('SHOPPING',
        'ltwebstatic.com shein.co.uk shein.com shein.se sheingsp.com sheinsz.ltwebstatic.com'),
    'shopee.com': ('SHOPPING',
        'shopee.cl shopee.cn shopee.co.id shopee.co.th shopee.com shopee.com.br shopee.com.co '
        'shopee.com.mx shopee.com.my shopee.es shopee.fr shopee.id shopee.in shopee.io shopee.ph '
        'shopee.sg shopee.tw shopee.vn shopeemobile.com shp.ee'),
    'liverpool.com.mx': ('SHOPPING',
        'liverpool.com.mx'),
    'walmart.com': ('SHOPPING',
        'walmart.com walmart.com.mx walmartimages.com walmartmexico.com'),
    'coppel.com': ('SHOPPING',
        'coppel.com'),
    'roblox.com': ('GAMING',
        'blox.com rbx.cn rbx.com rbxadder.com rbxcdn.com rbxcdn.net rbxinfra.com rbxinfra.net roblox.cn '
        'roblox.com roblox.qq.com robloxcdn.com robloxdev.cn robloxlabs.com'),
    'steampowered.com': ('GAMING',
        'csgo.wmsj.cn dl.steam.clngaa.com dl.steam.ksyna.com dota2.wmsj.cn playartifact.com s.team '
        'st.dl.bscstorage.net st.dl.eccdnx.com st.dl.pinyuncloud.com steam-api.com steam-chat.com '
        'steambroadcast.akamaized.net steamcdn-a.akamaihd.net steamchina.com '
        'steamcommunity-a.akamaihd.net steamcommunity.com steamcontent.com steamdeck.com steamgames.com '
        'steampipe.akamaized.net steampipe.steamcontent.tnkjmec.com steampowered.com '
        'steampowered.com.8686c.com steamserver.net steamstatic.com steamstatic.com.8686c.com '
        'steamstore-a.akamaihd.net steamusercontent-a.akamaihd.net steamusercontent.com '
        'steamuserimages-a.akamaihd.net steamvideo-a.akamaihd.net underlords.com valvesoftware.com '
        'wmsjsteam.com xz.pphimalayanrt.com'),
    'epicgames.com': ('GAMING',
        'eac-cdn.com easy.ac easyanticheat.net epicgames.com epicgames.dev unrealengine.com'),
    'riotgames.com': ('GAMING',
        'dradis-prod.rdatasrv.net pvp.net rdatasrv.net rgpub.io riotcdn.com riotcdn.net riotgames.com'),
    'leagueoflegends.com': ('GAMING',
        'leagueoflegends.co.kr leagueoflegends.com lol.riotgames.com lolstatic.com lolusercontent.com'),
    'battle.net': ('GAMING',
        'battle.net battlenet.com.cn blizzard.cn blizzard.com blizzardgames.cn blz-contentstack.com '
        'blzstatic.cn bnet.163.com bnet.cn'),
    'ea.com': ('GAMING',
        'accounts.ea.com cloudsync-prod.s3.amazonaws.com dawngate.com ea.com eamobile.com easports.com '
        'eastore.com lordofultima.com nearpolar.com origin-a.akamaihd.net origin.com origin.tv rtm.tnt- '
        'ea.com signin.ea.com ssl-lvlt.cdn.ea.com swtor.com tnt-ea.com'),
    'playstation.com': ('GAMING',
        'gaikai.com playstation-cloud.com playstation-cloud.net playstation.com playstation.net scea.com '
        'sonyentertainmentnetwork.com station.sony.com'),
    'xbox.com': ('GAMING',
        'gamepass.com xbox-global.ifs.windows.com xbox-guide-public.rec.mp.microsoft.com xbox.com '
        'xbox.ipv6.microsoft.com xboxab.com xboxab.net xboxlive.com xboxservices.com'),
    'nintendo.com': ('GAMING',
        'nintendo-europe.com nintendo.be nintendo.co.jp nintendo.co.uk nintendo.com nintendo.com.au '
        'nintendo.de nintendo.es nintendo.eu nintendo.fr nintendo.it nintendo.jp nintendo.net nintendo.nl '
        'nintendo.pt nintendoswitch.cn nintendowifi.net'),
    'x.com': ('SOCIAL',
        'ads-twitter.com cms-twdigitalassets.com periscope.tv pscp.tv t.co tellapart.com tweetdeck.com '
        'twimg.com twitpic.com twitter.biz twitter.com twitter.jp twittercommunity.com '
        'twitterflightschool.com twitterinc.com twitteroauth.com twitterstat.us twtrdns.net twttr.com '
        'twttr.net twvid.com vine.co x.com'),
    'google.com': ('WORK',
        '1e100.net google.com googleapis.com googleusercontent.com gstatic.com gvt1.com gvt2.com'),
    'icloud.com': ('SYSTEM',
        'apple-dns.net ess.apple.com icloud-content.com icloud.com mzstatic.com push.apple.com'),
    'windowsupdate.com': ('SYSTEM',
        'delivery.mp.microsoft.com mp.microsoft.com update.microsoft.com windowsupdate.com'),
    'anydesk.com': ('REMOTE',
        'anydesk.com net.anydesk.com'),
    'teamviewer.com': ('REMOTE',
        'teamviewer.com'),
    '4chan.org': ('SOCIAL',
        '4cdn.org 4chan.org 4channel.org'),
    '500px.com': ('SOCIAL',
        '500px.com 500px.org'),
    '9gag.com': ('SOCIAL',
        '9cache.com 9gag.com'),
    'activisionblizzard.com': ('GAMING',
        'activision.com activisionblizzard.com callofduty.com callofdutyleague.com codmwest.com '
        'demonware.net'),
    'aminoapps.com': ('SOCIAL',
        'aminoapps.com'),
    'betano.com': ('GAMING',
        'betano.bg betano.ca betano.com betano.cz betano.de betano.ng betano.pt'),
    'betfair.com': ('GAMING',
        'betfair.com betfair.com.au betfair.es betfair.it betfair.ro betfair.se'),
    'betway.com': ('GAMING',
        'betway.be betway.bet.ar betway.co.za betway.com betway.com.gh betway.com.ng betway.de betway.es '
        'betway.fr betway.it betway.mx betway.pl betway.se betwaygroup.com betwaysatta.com '
        'vietnambetway88.com'),
    'bigo.tv': ('STREAMING',
        'bigo.sg bigo.tv bigolive.tv bigovideo.tv'),
    'bilibili.com': ('STREAMING',
        'acg.tv acgvideo.com animetamashi.cn animetamashi.com anitama.cn anitama.net b23.tv bigfun.cn '
        'bili22.cn bili2233.cn bili23.cn bili33.cn biliapi.com biliapi.net bilibili.cc bilibili.cn '
        'bilibili.com bilibili.net bilibili.tv bilibiligame.cn bilibiligame.co bilibiligame.net '
        'bilibilipay.cn bilibilipay.com bilicdn1.com bilicdn2.com bilicdn3.com bilicdn4.com bilicdn5.com '
        'biligame.co biligame.com biligame.net biligo.com biliimg.com biliintl.com bilivideo.cn '
        'bilivideo.com bilivideo.net dreamcast.hk hdslb.com hdslb.org im9.com maoercdn.com mincdn.com '
        'upos-hz-mirrorakam.akamaized.net yo9.com'),
    'blaze.com': ('GAMING',
        'blaze.bet blaze.com blaze.com.br blazecareers.com'),
    'bsky.app': ('SOCIAL',
        'bsky.app bsky.social'),
    'globo.com': ('STREAMING',
        'canaisglobo.globo.com globosat.globo.com gsatmulti.globo.com'),
    'clarovideo.com': ('STREAMING',
        'claromusica.com clarovideo.com'),
    'clubhouse.com': ('SOCIAL',
        'clubhouse.com clubhouseapi.com'),
    'coolapk.com': ('SHOPPING',
        'coolapk.com coolapkmarket.com coolapkmarket.net'),
    'crunchyroll.com': ('STREAMING',
        'crunchyroll.com gccrunchyroll.com'),
    'dailymotion.com': ('STREAMING',
        'dailymotion.com dm-event.net dmcdn.net'),
    'deezer.com': ('STREAMING',
        'deezer.com dzcdn.net'),
    'directvgo.com': ('STREAMING',
        'directvgo.com'),
    'discoveryplus.com': ('STREAMING',
        'disco-api.com discoveryplus.com'),
    'dola.com': ('AI',
        'cici.com ciciai.com dola.com'),
    'douban.com': ('SOCIAL',
        'douban.com douban.fm doubanio.com'),
    'ebay.com': ('SHOPPING',
        '21centuryaccess.com 4ebaytraders.com adcommerce.cn adcommerce.tv appforebay.cn appsonebay.net '
        'asebay.com baazee.com bidbay.com bidorbuyindia.com billpoint.com billpoint.info billpoint.tv '
        'billpoint.us billpointnewzealand.com blogebay.com bookclubcorner.com builtfromebay.com '
        'buyitnow.com buyitnow.net buyitnow.org buyitnow.tv buyitnowshop.net cafr.ca carebay.com '
        'cargigileads.com cebay.com collective99.com commerceos.com connectcommerce.cn '
        'connectcommerce.com.cn connectcommerce.hk connectcommerce.info connectcommerce.tv '
        'connectedcommerce.cn connectedcommerce.com connectedcommerce.tv crececonebay.com '
        'creditcardsbay.com cyber-bay.cn cyber-bay.com.cn cyber-bay.info cyber-bay.org dba.dk dealbay.com '
        'dealtime.com didce.com douya.org dreamtoplay.com e-bay.com e-bay.it e-bay.net eachpay.com '
        'eachpay.net ebahy.com ebay-authenticate.net ebay-confirm.com ebay-course.com ebay-cz.com ebay- '
        'delivery.com ebay-discoveries.com ebay-fashion.com ebay-inc.com ebay-inc.net ebay-inc.org ebay- '
        'online.com ebay-sales.com ebay-stories.com ebay-us.com ebay-vacation.com ebay.at ebay.be ebay.ca '
        'ebay.ch ebay.cn ebay.co.nz ebay.co.uk ebay.co.ve ebay.co.za ebay.com ebay.com.ar ebay.com.au '
        'ebay.com.cn ebay.com.ec ebay.com.hk ebay.com.mt ebay.com.my ebay.com.ph ebay.com.sg ebay.de '
        'ebay.es ebay.fr ebay.ie ebay.in ebay.it ebay.jp ebay.lt ebay.mn ebay.net.cn ebay.nl ebay.org '
        'ebay.org.cn ebay.ph ebay.pk ebay.pl ebay.sg ebay.us ebay.vn ebay.yn.cn ebay.zj.cn ebay25.com '
        'ebay68.com ebaya.com ebayads.com ebayads.net ebayadvertising.cn ebayadvertising.com '
        'ebayanunsios.net ebayauction.com ebayaustralia.com ebayauthenticate.com.cn ebaybags.com '
        'ebaybank.com ebaybenefits.com ebayboutique.com ebayca.com ebayca.org ebaycafe.com ebaycar.com '
        'ebaycareers.com ebaycbt.co.kr ebaycdn.net ebaychina.net ebayclassifieds.cn ebayclassifieds.com '
        'ebayclassifieds.com.cn ebayclassifieds.info ebayclassifieds.org ebayclassifieds.tv '
        'ebayclassifiedsgroup.com ebayclassifiedsgroup.info ebayclassifiedsgroup.org ebayclassifies.com '
        'ebayclub.com ebaycoins.com ebaycom.com ebaycommercenetwork.com ebaycourse.com ebayd.com '
        'ebayde.com ebaydesc.cn ebaydesc.com.cn ebaydlassifieds.com ebaydns.cn ebaydts.com ebayedu.com '
        'ebayeletro.com ebayenterprise.cn ebayenterprise.com ebayenterprise.com.cn ebayenterprise.info '
        'ebayenterprise.net ebayenterprise.tv ebayetc.com ebayexpress.sg ebayfashion.com ebayfashion.net '
        'ebayforcharity.org ebayforeclosure.org ebayfrance.com ebayglobalshipping.com ebaygroup.com '
        'ebayhabit.com ebayheels.com ebayhots.com ebayimg.com ebayinc.com ebayinc.net ebayinc.org '
        'ebayincconnectedcommerce.net ebayinkblog.com ebayinternetsalestax.com ebayit.com ebayjewelry.com '
        'ebayjob.com ebayla.org ebaylisting.com ebaylocal.net ebaylocationsdevacances.com ebaymag.com '
        'ebaymainstreet.com ebaymall.com ebaymarketplace.net ebaymotors.ca ebaymotors.cn ebaymotors.com '
        'ebaymotors.com.cn ebaymotors.org ebaymotorsblog.com ebaynow.com ebaynyc.com ebayon.com '
        'ebayon.net ebayoncampus.com ebayopen.com ebayopensource.com ebayopensource.net ebaypakistan.net '
        'ebaypark.com ebayparts.com ebaypedia.cn ebaypedia.com.cn ebayprivacycenter.com ebayqq.com '
        'ebayradio.com ebayrtm.com ebayseller.com ebayshoesstore.com ebayshop.com ebayshop111.com '
        'ebayshopping.cn ebayshopping.com.cn ebayshopping.org ebaysocial.com ebaysocial.ru ebaysoho.com '
        'ebaysohos.com ebaystatic.cn ebaystatic.com ebaystore.com ebaystore77.com ebaystores.cn '
        'ebaystyle.com ebaysweden.com ebayt.com ebaytechblog.com ebaytopratedseller.net ebaytrading.com '
        'ebaytradingassistant.com ebaytv.org ebayuae.net ebayvakantiehuizen.com ebayvalet.com '
        'ebayvietnam.net ebayworlds.com ebayy.com edisebay.com eebay.com epinions.com eu-consumer- '
        'empowerment.com expertmaker.com fairmarket.com fragrancebay.com francemail.com half.com '
        'half.com.cn half.tv halfcanada.com halfjapan.com handbagsoutletebay.com iebay.com irribay.com '
        'itsbetterwhenyouwinit.com liketwice.com liveauction.com milofetch.com musicbay.net '
        'myconstructionworld.net myebay.com nebay.net paisapay.cc paisapay.info paisapay.tv premobay.com '
        'privatemarketplaces.net privatemarketplaces.us prostores.cn prostores.com prostores.com.cn '
        'rethink.net shopibay.net shoping.com sourcingforebay.com.cn sourcingforebay.net '
        'sourcingforebay.tv speybay.com storesense.com svpply.com telebay.com telesell.com texttobuy.org '
        'theebayshop.com theopportunityproject.org towerauction.com vendu.com watch-ebay.org '
        'weareebay.com wwwdecide.com wwwebay.com wwwebay.net wwwwebay.com xindelu.com xn-- '
        '3et96bj49ahpq.com xn--4vq475g.com xn--4vq477m.com xn--7hv594h.com xn--7hvy28f.cn xn--hb4aw0g.com '
        'xn--q41am8x.com xn--qoq462m.com xn--tkry91n.com xn--ubt498knmf.com xn--xsq421m.com xn-- '
        'xsq605n.com xn--xsq959n.com xn--yf1at58a.com xxbay.com yibei.org'),
    'espn.com': ('STREAMING',
        'es.pn espn.cl espn.co.uk espn.com espn.com.ar espn.com.au espn.com.co espn.com.ec espn.com.mx '
        'espn.com.pa espn.com.pe espn.com.uy espn.com.ve espn.in espn.net espncdn.com espncricinfo.com'),
    'fdjunited.com': ('GAMING',
        '32red.co.uk 32red.com aleda.fr bingo.com fdj.fr fdjunited.com fdjunitedaffiliates.com '
        'laddition.com lottery.ie mariacasino.dk mariacasino.ee mariacasino.se nirio.fr ottocasino.com '
        'sportinggroup.com sportingindex.com storspelare.se unibet.be unibet.co.uk unibet.dk unibet.ee '
        'unibet.fr unibet.it unibet.nl unibet.ro unibet.se vladcazino.ro zeturf.fr'),
    'fifa.com': ('STREAMING',
        'fifa.com fifaplus.com'),
    'flickr.com': ('CLOUD',
        'flic.kr flickr.com flickr.net flickrprints.com flickrpro.com staticflickr.com'),
    'globoplay.globo.com': ('STREAMING',
        'cloud-jarvis.globo.com globoplay.com globoplay.com.br globoplay.globo.com'),
    'gog.com': ('GAMING',
        'gog-cdn-lumen.secure2.footprint.net gog-statics.com gog.com gogalaxy.com'),
    'grindr.com': ('SOCIAL',
        'grindr.com grindr.mobi'),
    'grok.com': ('AI',
        'grok.com x.ai'),
    'hulu.com': ('STREAMING',
        'hulu.com'),
    'iheart.com': ('STREAMING',
        '937theriver.com iheart.com iheart.mx iheartmedia.com iheartradio.ca iheartradio.co.nz '
        'iheartradio.com ihrdev.com ihrhls.com ihrint.com ihrstage.com'),
    'imgur.com': ('CLOUD',
        'imgur.com'),
    'ioi.dk': ('GAMING',
        'hitman.com hitman.io ioi.dk'),
    'iqiyi.com': ('STREAMING',
        'iq.com iqiyi.com iqiyipic.com pps.tv ppsimg.com qiyi.com qiyipic.com qy.net'),
    'kakao.com': ('MESSAGING',
        'kakao.com kgslb.com'),
    'kik.com': ('MESSAGING',
        'kik.com'),
    'kookapp.cn': ('SOCIAL',
        'kaiheila.cn kookapp.cn'),
    'lazada.com': ('SHOPPING',
        'k1-lazadasg-oversea.gslb.ksyuncdn.com lazada.co.id lazada.co.th lazada.com lazada.com.my '
        'lazada.com.ph lazada.sg lazada.vn slatic.net'),
    'line.me': ('SOCIAL',
        'gcld-line.com lin.ee line-apps-beta.com line-apps-rc.com line-apps.com line-cdn.net line- '
        'scdn.net line.biz line.me line.naver.jp linecorp.com linefriends.com linefriends.com.tw '
        'linegame.jp linemobile.com linemyshop.com lineshoppingseller.com linetv.tw'),
    'linkedin.com': ('SOCIAL',
        'bizographics.com cs1404.wpc.epsiloncdn.net cs767.wpc.epsiloncdn.net l-0005.dc-msedge.net '
        'l-0005.l-dc-msedge.net l-0005.l-msedge.net l-0015.l-msedge.net licdn.cn licdn.com linkedin.at '
        'linkedin.be linkedin.cn linkedin.com linkedin.nl linkedin.qtlcdn.com lnkd.in'),
    'lionsgateplus.com': ('STREAMING',
        'lionsgateplus.com starz.com'),
    'looke.com.br': ('STREAMING',
        'looke.com.br ottvs.com.br'),
    'mail.ru': ('SOCIAL',
        'imgsmail.ru mail.ru mycdn.me'),
    'manus.im': ('AI',
        'manus.im manuscdn.com'),
    'mastodon.social': ('SOCIAL',
        'aus.social awscommunity.social climatejustice.social cupoftea.social cyberplace.social '
        'defcon.social det.social glasgow.social h4.io hachyderm.io hessen.social hostux.social ieji.de '
        'indieweb.social infosec.exchange ioc.exchange kolektiva.social livellosegreto.it lor.sh lou.lt '
        'm.cmx.im mas.to masto.ai masto.es masto.nu masto.pt mastodon.au mastodon.bida.im mastodon.com.tr '
        'mastodon.eus mastodon.green mastodon.ie mastodon.iriseden.eu mastodon.nl mastodon.nu mastodon.nz '
        'mastodon.online mastodon.scot mastodon.sdf.org mastodon.social mastodon.top mastodon.uno '
        'mastodon.world mastodon.zaclys.com mastodonapp.uk mastodont.cat mastodontech.de mastodontti.fi '
        'mastouille.fr mathstodon.xyz metalhead.club mindly.social mstdn.ca mstdn.jp mstdn.party '
        'mstdn.plus mstdn.social muenchen.social muenster.im nerdculture.de noc.social norden.social '
        'nrw.social o3o.ca ohai.social piaille.fr pol.social ravenation.club rollenspiel.social '
        'ruby.social ruhr.social sfba.social socel.net social.anoxinon.de social.cologne social.dev- '
        'wiki.de social.linux.pizza social.politicaconciencia.org social.vivaldi.net stranger.social '
        'sueden.social tech.lgbt techhub.social theblower.au tkz.one todon.eu toot.aquilenet.fr '
        'toot.community toot.funami.tech toot.io toot.wales troet.cafe union.place universeodon.com '
        'urbanists.social wien.rocks wxw.moe'),
    'max.ru': ('MESSAGING',
        'max.ru'),
    'meta.ai': ('AI',
        'meta.ai'),
    'minecraft.net': ('GAMING',
        'cubecraft.net megasmp.gg minecraft-services.net minecraft.net minecraftservices.com mojang.com'),
    'nebula.tv': ('STREAMING',
        'nebula.app nebula.tv'),
    'nvidia.com': ('WORK',
        'geforce.com geforcenow.com nvidia.cn nvidia.com nvidia.com.global.ogslb.com nvidia.eu '
        'nvidia.partners nvidiagrid.net nvidianews.com tegrazone.com'),
    'odysee.com': ('SOCIAL',
        'odycdn.com odysee.com odysee.live odysee.tv'),
    'ok.ru': ('SOCIAL',
        'insideok.ru ok.games ok.ru okcdn.ru oktech.ru st.mycdn.me'),
    'olvid.io': ('MESSAGING',
        'olvid-attachment-chunks.s3.eu-west-3.amazonaws.com olvid.io'),
    'onlyfans.com': ('SOCIAL',
        'onlyfans.com'),
    'paramountplus.com': ('STREAMING',
        'paramountplus.com pplusstatic.com'),
    'peacocktv.com': ('STREAMING',
        'peacock.com peacocktv.com'),
    'pinterest.com': ('SOCIAL',
        'pin.it pinimg.com pinterest.at pinterest.be pinterest.ca pinterest.ch pinterest.cl pinterest.co '
        'pinterest.co.at pinterest.co.in pinterest.co.kr pinterest.co.nz pinterest.co.uk pinterest.com '
        'pinterest.com.au pinterest.com.bo pinterest.com.ec pinterest.com.mx pinterest.com.pe '
        'pinterest.com.py pinterest.com.uy pinterest.com.vn pinterest.de pinterest.dk pinterest.ec '
        'pinterest.engineering pinterest.es pinterest.fr pinterest.hu pinterest.id pinterest.ie '
        'pinterest.in pinterest.info pinterest.it pinterest.jp pinterest.kr pinterest.mx pinterest.nl '
        'pinterest.nz pinterest.pe pinterest.ph pinterest.pt pinterest.ru pinterest.se pinterest.th '
        'pinterest.tw pinterest.uk pinterest.vn pinterestmail.com'),
    'play.google.com': ('WORK',
        'play-fe.googleapis.com play-lh.googleusercontent.com prod-lt-playstoregatewayadapter- '
        'pa.googleapis.com'),
    'pof.com': ('SOCIAL',
        'pof.com'),
    'plex.tv': ('STREAMING',
        'plex.bz plex.direct plex.tv plexapp.com'),
    'pluto.tv': ('STREAMING',
        'pluto.tv'),
    'privacy.com.br': ('CLOUD',
        'privacy.com.br'),
    'proton.me': ('CLOUD',
        'pm.me proton.me protonmail.ch protonmail.com protonvpn.com'),
    'qq.com': ('STREAMING',
        'qq-video.cdn-go.cn url.cn'),
    'questionai.ai': ('AI',
        'questionai.ai questionai.com'),
    'qwen.ai': ('AI',
        'qwen.ai qwenlm.ai'),
    'viki.com': ('STREAMING',
        'm-content-viki.s.llnwi.net viki.com viki.io'),
    'reddit.com': ('SOCIAL',
        'redd.it reddit.com redditmail.com redditmedia.com redditstatic.com'),
    'rockstargames.com': ('GAMING',
        'rockstargames.com rsg.sc'),
    'samsungtvplus.com': ('STREAMING',
        'internetat.tv samsung.wurl.tv samsungcloud.tv samsungtvplus.com'),
    'shellshock.io': ('GAMING',
        'algebra.best algebra.monster algebra.vip biologyclass.club combateggs.com deadlyegg.com '
        'deathegg.life deathegg.world egg.dance eggbattle.com eggboy.club eggboy.me eggboy.xyz '
        'eggcombat.com eggfacts.fun egggames.best egghead.institute eggisthenewblack.com eggsarecool.com '
        'eggshock.com eggshock.me eggshock.net eggshooter.best eggshooter.com eggwarfare.com eggwars.io '
        'geometry.best geometry.monster geometry.pw geometry.report hardboiled.life hardshell.life '
        'humanorganising.org math.international mathactivity.club mathactivity.xyz mathdrills.info '
        'mathdrills.life mathfun.rocks mathgames.world mathlete.fun mathlete.pro overeasy.club '
        'risenegg.com scrambled.tech scrambled.today scrambled.us scrambled.world shellgame.me '
        'shellplay.live shellshock.guru shellshock.io shellshockers.best shellshockers.ca '
        'shellshockers.club shellshockers.life shellshockers.site shellshockers.today shellshockers.us '
        'shellshockers.website shellshockers.wiki shellshockers.world shellshockers.xyz shellsocks.com '
        'softboiled.club urbanegger.com violentegg.club violentegg.fun yolk.best yolk.life yolk.monster '
        'yolk.quest yolk.rocks yolk.tech yolk.today zygote.cafe'),
    'signal.org': ('MESSAGING',
        'signal.org whispersystems.org'),
    'skype.com': ('MESSAGING',
        'edge-skype-com.s-0001.s-msedge.net skype-edf.akadns.net skype.com skype.net skypeassets.com '
        'skypeassets.net skypedata.akadns.net'),
    'snapchat.com': ('SOCIAL',
        'impala-media-production.s3.amazonaws.com sc-cdn.net snap-dev.net snapads.com snapchat.com '
        'snapkit.co'),
    'soundcloud.com': ('STREAMING',
        'sndcdn.com soundcloud.com'),
    'tidal.com': ('STREAMING',
        'tidal.com'),
    'tinder.com': ('SOCIAL',
        'gotinder.com tinder.com tindersparks.com'),
    'tumblr.com': ('SOCIAL',
        'tumblr.com'),
    'ubisoft.com': ('GAMING',
        'ubi.com ubisoft.com ubisoft.org ubisoftconnect.com'),
    'playvalorant.com': ('GAMING',
        'playvalorant.com valorant.scd.riotcdn.net valorant.secure.dyn.riotcdn.net'),
    'viber.com': ('MESSAGING',
        'viber.com'),
    'vimeo.com': ('STREAMING',
        'livestream.com vhx.tv vhxqa1.com vhxqa2.com vhxqa3.com vhxqa4.com vhxqa6.com vimeo-staging.com '
        'vimeo-staging2.com vimeo.com vimeo.fr vimeobusiness.com vimeocdn.com vimeogoods.com '
        'vimeoondemand.com vimeostatus.com'),
    'vivoplay.com.br': ('STREAMING',
        'vivoplay.com.br vivoplay.net vivotv.com.br'),
    'vk.com': ('SOCIAL',
        'mvk.com userapi.com vk-cdn.me vk-cdn.net vk-portal.net vk.cc vk.com vk.design vk.link vk.me '
        'vkcache.com vkgo.app vklive.app vkmessenger.app vkmessenger.com vkontakte.ru vkuseraudio.com '
        'vkuserlive.net vkuservideo.com vkuservideo.net'),
    'voot.com': ('STREAMING',
        'voot.com'),
    'wargaming.net': ('GAMING',
        'wargaming.com wargaming.net wgcdn.co wgcrowd.io worldoftanks.com worldofwarplanes.com '
        'worldofwarships.eu wotblitz.com'),
    'warnerbrosgames.com': ('GAMING',
        'warnerbrosgames.com'),
    'wechat.com': ('MESSAGING',
        'wechat.com weixin.qq.com weixin.qq.com.cn weixinbridge.com wx.qq.com'),
    'weibo.com': ('STREAMING',
        'wbimg.cn wbimg.com wcdn.cn weibo.cn weibo.com weibo.com.cn weibocdn.com'),
    'wizzapp.com': ('SOCIAL',
        'getwizz.io wizz.chat wizzapp.com'),
    'xiaohongshu.com': ('SHOPPING',
        'xhscdn.com xhscdn.net xiaohongshu.com xiaohongshu.com.my xiaohongshu.net'),
    'yy.com': ('STREAMING',
        'yy.com'),
    'zhihu.com': ('SOCIAL',
        'zhihu.com zhimg.com'),
}

# Dominio/categoría por regla: {sufijo_de_dominio: (dominio_canónico, CATEGORÍA)} — O(1) por etiqueta.
ADGUARD_DOMAIN_CATALOG: dict[str, tuple[str, str]] = {
    _dom: (_canon, _cat) for _canon, (_cat, _doms) in _SERVICE_RULES.items() for _dom in _doms.split()
}

# Prefijos IPv4 conocidos (cuando DoH oculta el DNS y solo se ve la IP remota). Clave = prefijo con punto final.
_NDPI_IP_GROUPS = (
    (("facebook.com", "SOCIAL"), "157.240. 31.13. 69.171. 69.63. 173.252. 185.60.216. 129.134. 179.60.192."),
    (("netflix.com", "STREAMING"), "23.246. 37.77.184. 45.57. 64.120.128. 66.197.128. 108.175.32. 185.2.220. 198.38.96. 198.45.48. 208.75.76."),
    (("telegram.org", "MESSAGING"), "149.154.160-175. 91.108.4-19. 91.108.56-59."),
    (("twitch.tv", "STREAMING"), "52.223.192. 99.181.64. 192.16.64. 199.9.248."),
    (("spotify.com", "STREAMING"), "35.186.224. 104.154.127."),
    (("roblox.com", "GAMING"), "128.116."),
    (("steampowered.com", "GAMING"), "155.133. 162.254.192. 103.28.54."),
    (("google.com", "WORK"), "142.250. 142.251. 172.217. 216.58. 74.125. 172.253. 209.85. 108.177."),
    (("microsoft.com", "WORK"), "13.107. 20.190. 40.96. 40.97. 40.98. 40.99. 40.100. 40.101. 40.102. 40.126. 52.96. 52.97. 52.98."),
    (("cloudflare.com", "CLOUD"),
     "104.16. 104.17. 104.18. 104.19. 104.20. 104.21. 104.22. 104.24. 104.25. 104.26. 104.27. 172.64. 172.67. 1.1.1. 1.0.0."),
)


def _expand_ip_prefix(prefix: str):
    """'149.154.160-175.' -> '149.154.160.', ..., '149.154.175.' (rango del último octeto del prefijo)."""
    head, _, last = prefix.rstrip(".").rpartition(".")
    if "-" not in last:
        yield prefix
        return
    lo, hi = last.split("-")
    for n in range(int(lo), int(hi) + 1):
        yield f"{head}.{n}."


NDPI_IP_PREFIX_CATALOG: dict[str, tuple[str, str]] = {
    _p: _svc for _svc, _prefixes in _NDPI_IP_GROUPS for _raw in _prefixes.split() for _p in _expand_ip_prefix(_raw)
}

# Categorías "de servicio" que nunca deben ser pisadas por entradas DNS genéricas (Google/Cloudflare/Akamai).
SERVICE_PRIORITY_CATEGORIES = ("STREAMING", "SOCIAL", "MESSAGING", "AI", "GAMING", "SHOPPING")


def lookup_service_catalog(raw_domain_or_ip: str):
    """Busca solo en los catálogos (sin heurísticas). Retorna (dominio_canónico, categoría) o None."""
    val = (raw_domain_or_ip or "").strip().lower().rstrip(".")
    if not val:
        return None
    parts = val.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        hit = NDPI_IP_PREFIX_CATALOG.get(".".join(parts[:3]) + ".")
        return hit or NDPI_IP_PREFIX_CATALOG.get(".".join(parts[:2]) + ".")
    for i in range(len(parts) - 1):  # a.b.c.com -> b.c.com -> c.com
        hit = ADGUARD_DOMAIN_CATALOG.get(".".join(parts[i:]))
        if hit:
            return hit
    return None


def match_service_domain_or_ip(raw_domain_or_ip: str, app_name: str = "") -> tuple[str, str]:
    """
    Normaliza un dominio/CDN/IP al servicio canónico y su categoría ejecutiva.
    Retorna (clean_domain, category_code). Orden: catálogo AdGuard (sufijos) -> prefijos IP nDPI ->
    dominio base + categoría por aplicación.
    """
    val = (raw_domain_or_ip or "").strip().lower().rstrip(".")
    if not val:
        return "", "OTHER"
    hit = lookup_service_catalog(val)
    if hit:
        return hit

    # Descartar ruido técnico puro de infraestructura inversa (PTR de ISP genéricos o ARPA)
    if val.endswith((".in-addr.arpa", ".ip6.arpa", ".local", ".lan", ".home", ".internal")):
        return "", "OTHER"

    # Dominio estándar (no IP): reducir subdominios excesivos al dominio base legible
    parts = [p for p in val.split(".") if p]
    is_ipv4 = len(parts) == 4 and all(p.isdigit() for p in parts)
    if not is_ipv4 and ":" not in val and len(parts) >= 3:
        if len(parts[-1]) == 2 and parts[-2] in ("com", "net", "org", "gob", "edu", "co"):
            val = ".".join(parts[-3:])
        elif parts[-3] in ("web", "mail", "chat", "drive", "docs", "meet", "teams", "api", "portal"):
            val = ".".join(parts[-3:])
        else:
            val = ".".join(parts[-2:])

    app_low = (app_name or "").lower()
    if any(k in app_low for k in ("anydesk", "teamviewer", "rustdesk", "mstsc")):
        return val, "REMOTE"
    if any(k in app_low for k in ("spotify", "vlc", "music")):
        return val, "STREAMING"
    if any(k in app_low for k in ("onedrive", "dropbox", "googledrive")):
        return val, "CLOUD"
    if any(k in app_low for k in ("code", "cursor", "antigravity", "pycharm", "terminal", "iterm", "git", "ssh")):
        return val, "WORK"

    if any(k in val for k in ("video", "stream", "media", "cdn")):
        return val, "STREAMING"
    if any(k in val for k in ("google.com", "gstatic.com", "googleapis.com", "microsoft.com", "bing.com", "cloudflare.com", "amazonaws.com", "akamai")):
        return val, "WORK"
    if any(k in val for k in ("apple.com", "digicert.com", "ocsp", "msn.com", "live.com")):
        return val, "SYSTEM"
    return val, "OTHER"
