# SK

suleymankaynak.com sitesinin statik kopyası (HTML, CSS, JS, görseller, dokümanlar).

- `site/`: sitenin kopyası. Dosyalar sunucudan geldiği gibi, bayt bayt aynı kaydedilir. PHP ve WordPress paneli yoktur.
- `report/rapor.md`: son indirmenin raporu (eksikler, dış kaynaklar, şüpheli kod belirtileri).
- `report/manifest.json`: her dosyanın boyutu ve SHA-256 değeri.
- `scripts/mirror.py`: indirme scripti (Python, ek paket gerektirmez).
- `.github/workflows/mirror.yml`: Actions sekmesinde **Siteyi kopyala** → **Run workflow** ile yeniden indirir.
- `.github/workflows/canli-kontrol.yml`: Actions sekmesinde **Canlı kontrol** → **Run workflow**. IP alanına yeni sunucunun IP adresini yazarsan DNS değişmeden o sunucudaki site, repodaki kopyayla bayt bayt karşılaştırılır; boş bırakırsan DNS'in gösterdiği sunucu denenir.

Site olduğu gibi kopyalandığı için içindeki olası zararlı kodlar da kopyalanmıştır; temizlenmeden yayınlanmamalıdır
(rapordaki "Dikkat" bölümüne bakın).
