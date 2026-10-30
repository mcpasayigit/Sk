# SK

suleymankaynak.com sitesinin statik kopyası (HTML, CSS, JS, görseller, dokümanlar).

- `site/`: sitenin kopyası. Dosyalar sunucudan geldiği gibi, bayt bayt aynı kaydedilir. PHP ve WordPress paneli yoktur.
- `site/404.html`: sitenin kendi "sayfa bulunamadı" sayfası.
- `report/rapor.md`: son indirmenin raporu (eksikler, dış kaynaklar, şüpheli kod belirtileri).
- `report/manifest.json`: her dosyanın boyutu ve SHA-256 değeri.
- `report/kontrol_notlari.md`: kopyanın doğrulama sonuçları ve dikkat edilecekler.
- `scripts/mirror.py`: indirme scripti (Python, ek paket gerektirmez).
- `.github/workflows/mirror.yml`: Actions sekmesinde **Siteyi kopyala** → **Run workflow** ile yeniden indirir.
- `.github/workflows/404-sayfasi.yml`: **404 sayfasını al** → sitenin kendi 404 sayfasını `site/404.html` olarak kaydeder.
- `.github/workflows/canli-kontrol.yml`: Actions sekmesinde **Canlı kontrol** → **Run workflow**. IP alanına yeni sunucunun IP adresini yazarsan DNS değişmeden o sunucudaki site, repodaki kopyayla bayt bayt karşılaştırılır; boş bırakırsan DNS'in gösterdiği sunucu denenir. Sonuç, çalıştırmanın **Summary** sayfasında görünür.

## Veridyen / cPanel'e kurulum

- `.cpanel.yml`: cPanel > Git Version Control > **Deploy HEAD Commit** ile `site/` klasörünü `/home/sulo/public_html/` altına kopyalar. cPanel kullanıcı adı `sulo` değilse `DEPLOYPATH` satırını değiştir.
- `deploy/htaccess`: `public_html/.htaccess` olarak kopyalanır. `www` adresini `suleymankaynak.com`a, `http` adresini `https`e yönlendirir ve 404 sayfasını ayarlar.
- `main` dalına zorla gönderim (force push) yapma: cPanel "Update from Remote" yalnızca ileri sarmayı (fast-forward) kabul eder.
- Disk: site 1,1 GB. Klon + çalışma kopyası + `public_html` birlikte yaklaşık 3,3 GB tutar; hesaba en az 4 GB kota ver.
- Alan adının MX kaydı alan adının kendisini gösteriyor (e-posta web sunucusuyla aynı yerde). DNS'te A kaydını değiştirmeden önce e-posta için ayrı bir karar gerekir; ayrıntı `report/kontrol_notlari.md` içinde.

## Dikkat: kopyadaki zararlı script

`site/blog/index.html` ve `site/gallery/index.html` sayfalarının içeriğine siteye sonradan dışarıdan `<script>` etiketleri eklenmiş
(`line.storerightdesicion.com`, `refer.specialadves.com`). Kopya olduğu gibi alındığı için bu etiketler de kopyada duruyor ve
sayfayı açan herkesin tarayıcısı bu adreslerden kod yükler. Ayrıntı ve satır numaraları `report/kontrol_notlari.md` içinde.
Başka şüpheli kod bulunmadı. Rapordaki "Dikkat" bölümündeki `curriculum-vitae` uyarısı yanlış alarmdı ("specialist" kelimesi).
