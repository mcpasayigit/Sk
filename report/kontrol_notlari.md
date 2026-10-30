# suleymankaynak.com kopyası: kontrol notları (2026-10-30)

## Kontrol sonuçları

| Kontrol | Sonuç |
| --- | --- |
| Sitemap'teki sayfalar | 42 adresin 42'si kopyada |
| Dosya bütünlüğü | 2485 dosyanın 2485'i manifest'teki SHA-256 ile eşleşti, eksik ya da uyuşmayan yok |
| Görseller | 2170 görsel çözümlendi, bozuk ya da kesik yok |
| Tarayıcı (Chromium, kopyadan sunularak) | 42 sayfanın 42'si açıldı, içeriği dolu, JavaScript istisnası yok, site dışına atan sayfa yok |
| Galeri ve sertifika sayfaları | `/galeriler/` (176 görsel), `/gallery/` (163), `/sertifikalar/` (441) görselleriyle yükleniyor |
| Kendi alan adına `http://` ile başvurular | 581 görsel (tarayıcı bunları otomatik `https`e çeviriyor) ve 4 pdf gömme (`object`) başvurusu (orijinal sitede de aynı). CSS ve JS için `http://` başvurusu yok |

## Orijinal sitede de bozuk olanlar (kopya hatası değil)

Sunucu bunları kopyalama sırasında 404 verdi; kopya olduğu gibi aynı eksiklerle çalışır.

- `lightview-plus` eklentisinin 3 dosyası (1 CSS, 2 JS). Her sayfa başvuruyor, tarayıcı konsolunda 404 görünür, sayfaların görünümü bozulmuyor.
- `/galeri/<ad>` altındaki 11 galeri detay sayfası (`galeriler` ve `gallery` sayfalarındaki bağlantılar bunlara gidiyor).
- `/resimler/basindan.png`: `basindan-secmeler` ve `press` sayfalarında bir görsel kırık.
- `/resim/uyeliklervegorevler.pdf` ve iki tema görseli (`bg-input.png`, `bg-input-focus.png`).
- `wp-content/plugins/responsive-lightbox/font/responsive-lightbox.svg` sunucuda 0 bayt.
- Paylaşım bağlantıları: `...&title=...` ile biten 15 adres 404 veriyor.

## Zararlı kod

Tüm HTML (191) ve JS (30) dosyaları tarandı. Dışarıdan eklenmiş zararlı kod yalnızca iki sayfanın yazı içeriğinde:

| Dosya | Satır | Eklenen |
| --- | --- | --- |
| `site/blog/index.html` | 491 | `line.storerightdesicion.com/ping/?str.js` + 5 kez `refer.specialadves.com/stack.js?ver=12.3.4` |
| `site/gallery/index.html` | 1009 | `line.storerightdesicion.com/ping/?str.js` + 4 kez `refer.specialadves.com/stack.js?ver=12.3.4` |

Bunlar WordPress veritabanındaki yazı içeriğine eklenmiş (temaya değil), bu yüzden diğer sayfalarda yok. Kopya olduğu gibi
alındığı için `site/` içinde duruyor; yayınlanırsa bu iki sayfayı açan ziyaretçinin tarayıcısı bu adreslerden kod yükler.
Dosyalarda başka şüpheli işaret (`eval`, `atob`, gizli bağlantı bloğu, `meta refresh`, uzun şifreli blok) yok.

Diğer dış kaynaklar normal: Google Fonts, Google Maps, Google Docs/Forms iframe'leri, `gozsagligi.todnet.org` (2 görsel),
`gmpg.org` (WordPress profil bağlantısı) ve temanın demo sitesine bir CSS başvurusu (`cdn.demo.runathemes.com`).

## E-posta (DNS) uyarısı

DNS: `A` ve `www` kayıtları `185.216.113.152` (IHS) adresini gösteriyor, ad sunucuları `ns1/ns2.pasayigit.company`.
`MX` kaydı `0 suleymankaynak.com.`, yani posta alan adının kendisine, web sunucusuyla aynı adrese gidiyor.

`A` kaydını Veridyen IP'sine çevirdiğin an gelen postalar da Veridyen'e gider ve IHS'deki posta kutularına ulaşmaz.
Geçişten önce seçilecek yol:

1. Posta IHS'de kalsın: `mail.suleymankaynak.com` için `A` kaydını `185.216.113.152` olarak ekle, `MX` kaydını `0 mail.suleymankaynak.com.` yap,
   sonra yalnızca ana `A` ve `www` kayıtlarını Veridyen'e çevir. SPF (`TXT`) kaydında `a` kullanılıyorsa gözden geçir.
2. Postayı da Veridyen'e taşı: cPanel'de posta hesaplarını aç, eski kutuları aktar, sonra DNS'i çevir.

Güncel DNS kayıtları (A, NS, MX, TXT, www, mail) **Canlı kontrol** çalıştırmasının Summary sayfasında listelenir.
