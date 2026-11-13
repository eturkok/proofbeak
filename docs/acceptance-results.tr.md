# Uçtan uca deneme sonuçları

13 Kasım 2026 tarihinde, **Proofbeak 0.5.1** ile 12 farklı senaryo ve
24 aday gözlemi çalıştırıldı. Koşul sonuçları: **17 pass, 4 fail,
3 inconclusive**. Otomasyon, gözlenen sonuçların önceden tanımlanan
referanslarla eşleştiğini ve operasyonel kontrollerin geçtiğini doğruladı.

[Gerçek CI çalıştırması](https://github.com/eturkok/proofbeak/actions/runs/43433333164) ·
[Ham kanıt paketi](https://github.com/eturkok/proofbeak/actions/runs/43433333164/artifacts/14040811048) ·
[Ölçülen JSON sonuçları ve hash'ler](acceptance-results.json) ·
[Tanımlar ve yeniden çalıştırma](acceptance-evaluation.md)

## Dosya servisleri

Bu bölüm, Docker içinde çalışan gerçek WsgiDAV ve Apache WebDAV kurulumlarını kullanır.
İki servis aynı kullanıcı tanımları ve mevcut `scenario.v1` işlemleriyle denendi.

| Senaryo | WsgiDAV | Apache |
| --- | --- | --- |
| Aynı içeriği tekrar yazma | `pass` | `pass` |
| Bir nesneyi güncellerken diğerini koruma | `pass` | `pass` |
| Bir nesneyi silerken diğerini koruma | `pass` | `pass` |
| Silmeyi tekrarlama | `pass` | `pass` |
| Olmayan anahtarı silme | `pass` | `pass` |
| Türkçe, emoji ve satır sonlarını koruma | `pass` | `pass` |
| Yedeği koruyarak asıl içeriği güncelleme | `pass` | `pass` |
| Üzerine yazma girişiminde ilk içeriği koruma | `fail` | `fail` |

Yedi koşulu iki aday da sağladı. Arşiv anahtarının ilk içeriğini koruma koşulunu iki aday da
sağlamadı: bu tanım için sonuç `no_match`. Bu sonuç, test edilen kurulum ve yazma işlemiyle
sınırlıdır; sağlayıcıların farklı yapılandırmalarına veya başka protokollerine genellenmez.

## HTTP JSON koşulları

A ve B, farklı yanıt biçimleri ve kasıtlı olarak farklı değerler sunan iki yerel test servisidir.
İstekler gerçek ağ üzerinden gönderildi. Sağlayıcıların gerçek JSON alanları ortak alanlara
eşlendi; eksik alanlara varsayılan değer eklenmedi.

| Senaryo | HTTP A | HTTP B |
| --- | --- | --- |
| Yanıtta hazır olduğunu bildirme | `pass` | `fail` |
| Yanıtta en az 1000 öğe kapasitesi bildirme | `fail` | `pass` |
| Yanıtta en az 30 gün saklama bildirme | `pass` | `inconclusive` |
| Yanıtta audit export desteğini doğrulama | `inconclusive` | `inconclusive` |

Hazır olma koşulunda A, kapasite koşulunda B uygun çıktı. Bunlar ayrı tanımlardır; ikisi birden
zorunluysa tek senaryoda birlikte aranmalıdır. Saklama alanı B'de eksikti; audit export alanı
ikisinde de yoktu. Bu nedenle eksik bilgiler `inconclusive` kaldı. Son soruda sonuç
`insufficient_evidence`; destek yoktur veya uygundur diye karar üretilmedi.

Bu okumalar, servis yanıtlarında **bildirilen değerleri** ölçer. 1000 öğe yükleme, 30 gün veri
saklama veya audit export işleminin kendisi bu HTTP denemeleriyle doğrulanmış değildir.

## Gerçekte çalıştırılan akış ve kontroller

- CLI ile yeni tenant, sahibi dışında okunamayan yapılandırma ve boş SQLite durumu oluşturuldu.
- Çevrimdışı yapılandırma kontrolü ve gerçek REST servisi çalıştırıldı.
- Gerçek MCP örnek istemcisi keşif, doğrulama, gönderim, worker sonucunu bekleme, karşılaştırma,
  bağımsız kanıt okuma, hash doğrulama ve JSON/HTML rapor dışa aktarmayı tamamladı.
- 16 WebDAV test koleksiyonunun temizliği doğrulandı. HTTP okumaları uzak kaynak oluşturmadı.
- Her WebDAV servisinde **45 değiştirici HTTP isteği** kaydedildi.
  Sayı, olmayan anahtarın silinmesi gibi 404 dönebilen idempotent denemeleri de kapsar;
  fiziksel disk yazması veya tam bir kez çalışma garantisi anlamına gelmez.
- Her HTTP test servisine **8 GET** gönderildi; değiştirici HTTP çağrısı yapılmadı.
- Aynı anahtarla tekrar çağrı ve API yeniden başlatma sonrasında özgün run, gözlem zamanları,
  ölçümler ve kanıtlar korundu. Tek değişebilen alan güncel geçerlilik kontrolünün
  `checked_at` zamanıydı. **Ek sağlayıcı yazması: 0.**

12 sorunun 10'unda en az bir aday koşulu sağladı; birinde aday yoktu, birinde kanıt yetersizdi.
Bunlar koşula bağlı sonuçlardır; genel bir servis sıralaması değildir.

## Kayıt ve sınırlar

Bu rapor yukarıda bağlantısı verilen başarılı ölçümü kaydeder. Manifestin SHA-256 değeri:

`03096e7fe1e1e33c2f7d3826288d00bd509cba7f444dff1ed0778febdc2bee83`

Her satırın test hash'i, run kimliği, aday kanıt hash'leri ve rapor dosyası hash'i
[JSON kaydında](acceptance-results.json) bulunur. CI ham kanıt paketi yedi gün tutulur;
bu kaydın paketi 20 Kasım 2026 tarihinde sona erer. Senaryolar ve kalıcı özet yeniden
çalıştırılabilir biçimde repoda tutulur.

Soruları ve referansları aynı geliştirici hazırladı. Bağımsız insan hakemler, gerçek müşteri
verileri, dış modelle A/B deneyi, kullanıcı onboarding süresi veya geri dönüş kullanım ölçümü
bu çalışmada yoktur. Bu çalışma teknik kullanım akışını doğrular; [insan pilotunun](pilot.md)
açık kalan ölçümlerini tamamlamış sayılmaz. Ürüne yeni test paketi veya işlem eklenmedi.

