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
- `.github/workflows/canli-kontrol.yml`: Actions sekmesinde **Canlı kontrol** → **Run workflow**. IP alanına yeni sunucunun IP adresini yazarsan DNS değişmeden o sunucudaki site, repodaki kopyayla bayt bayt karşılaştırılır; boş bırakırsan DNS'in gösterdiği sunucu denenir. **http** kutusunu işaretlersen dosyalar yalnızca HTTP (80) ile doğrulanır; sunucuda henüz sertifika yokken (DNS çevrilmeden önce) kullanılır. DNS çevrildikten sonra IP'yi boş bırakıp **bekle** (dakika) ve **beklenen_ip** yazarsan iş, DNS o IP'ye dönüp geçerli sertifika gelene kadar bekler, sonra tüm dosyaları https üzerinden doğrular. Sonuç, çalıştırmanın **Summary** sayfasında görünür.
- `.github/workflows/sunucu-tanisi.yml`: **Sunucu tanısı** → verilen IP'deki sunucunun alan adı için ne yanıt verdiğini gösterir (alan adı o IP'de tanımlı mı, cPanel geçici adresi). Sonuç **Annotations** bölümünde.

## Veridyen / cPanel'e kurulum

- `.cpanel.yml`: cPanel > Git Version Control > **Deploy HEAD Commit** ile `site/` klasörünü `/home/sulo/public_html/` altına kopyalar. cPanel kullanıcı adı `sulo` değilse `DEPLOYPATH` satırını değiştir.
- IP: hesabın IP'si cPanel > Genel Bilgiler > **Shared IP Address** satırında yazar (`78.142.210.231`). Aynı sunucudaki başka hesapların IP'si farklı olabilir (Retina hesabı `.233`); DNS'te ve kontrollerde hesabın kendi IP'sini kullan.
- `deploy/htaccess`: `public_html/.htaccess` olarak kopyalanır. `www` adresini `suleymankaynak.com`a, `http` adresini `https`e yönlendirir ve 404 sayfasını ayarlar.
- `main` dalına zorla gönderim (force push) yapma: cPanel "Update from Remote" yalnızca ileri sarmayı (fast-forward) kabul eder.
- Disk: site 1,1 GB. Klon + çalışma kopyası + `public_html` birlikte yaklaşık 3,3 GB tutar; hesaba en az 4 GB kota ver.
- Alan adının MX kaydı alan adının kendisini gösteriyor (e-posta web sunucusuyla aynı yerde). DNS'te A kaydını değiştirmeden önce e-posta için ayrı bir karar gerekir; ayrıntı `report/kontrol_notlari.md` içinde.

### Panelde Terminal yoksa veya "The system cannot deploy" yazıyorsa

`.cpanel.yml`'nin yaptığı kopyalamayı cPanel > **Cron Jobs** ile elle yapabilirsin: "Once Per Minute (* * * * *)" seç, aşağıdaki
tek satırı Command kutusuna yapıştır, ekle. Çıktı `/home/sulo/sk-durum.txt` dosyasına yazılır (tarih, HEAD, `git status`,
iki sayfanın boyutu: `blog` 55017, `gallery` 211509 bayt; sonda `TAMAM`). `TAMAM` görününce cron işini sil. Dosyada `TAMAM`
varken komut bir şey yapmaz. Kopyalama klonun çalışma kopyasından yapılır; klon bir tarayıcı tarafından değiştirilmişse
`git status` bunu gösterir.

```
grep -qs TAMAM /home/sulo/sk-durum.txt || { cd /home/sulo/repositories/sk && { date; git log -1 --oneline; git status --short | head -40; ls -l site/blog/index.html site/gallery/index.html; cp -a site/. /home/sulo/public_html/ && cp deploy/htaccess /home/sulo/public_html/.htaccess && rm -f /home/sulo/public_html/.nojekyll && echo TAMAM; du -sh /home/sulo/public_html; date; } > /home/sulo/sk-durum.txt 2>&1; }
```

### DNS çevrilmeden önce ön kontrol

AutoSSL, alan adı yeni sunucuya bakmadan sertifika veremez; o yüzden DNS'ten önce https denenemez. Actions > **Canlı kontrol** >
IP: `78.142.210.231`, **http** kutusu işaretli çalıştırınca 2485 dosyanın hepsi düz HTTP ile çekilip SHA-256 ile karşılaştırılır
(`X-Forwarded-Proto: https` başlığı, `deploy/htaccess`'in https yönlendirmesini atlatır; http→https, www→çıplak alan ve 404
davranışları yine denenir). Sertifika ve https bu kontrolde denenmez; onlar DNS'ten ve AutoSSL'den sonra **http** işaretsiz
çalıştırılan kontrolle (`bekle` seçeneğiyle) doğrulanır.

## Dikkat: kopyadaki zararlı script

`site/blog/index.html` ve `site/gallery/index.html` sayfalarının içeriğine siteye sonradan dışarıdan `<script>` etiketleri eklenmiş
(`line.storerightdesicion.com`, `refer.specialadves.com`). Kopya olduğu gibi alındığı için bu etiketler de kopyada duruyor ve
sayfayı açan herkesin tarayıcısı bu adreslerden kod yükler. Ayrıntı ve satır numaraları `report/kontrol_notlari.md` içinde.
Veridyen sunucusundaki zararlı yazılım tarayıcısı bu etiketleri klonda kendisi sildi (blog'da 6, gallery'de 5 etiket; 55017 → 54552 ve
211509 → 211118 bayt). cPanel bu yüzden klonu "değişmiş" sayıp Git panelinde "The system cannot deploy" dedi; dosyalar `.cpanel.yml`
yerine Cron İşi ile kopyalandı. Canlı site bu nedenle temiz sürümü yayınlar. `temiz` dalı bu temiz sürümdür (yalnızca o 11 etiket çıkarılmış,
`report/manifest.json` iki kayıt için güncel); sunucudakiyle bayt bayt aynıdır (ön kontrol: 2485/2485). Özgün (bulaşık) kopya `main` ve geçmişte durur.
Başka şüpheli kod bulunmadı. Rapordaki "Dikkat" bölümündeki `curriculum-vitae` uyarısı yanlış alarmdı ("specialist" kelimesi).
