# Proofbeak

**Bir servisin ihtiyacınız olan davranışı sağladığını test edin; sonucunu kanıtlarıyla görün.**

[English](README.md) · [Türkçe](README.tr.md)

Proofbeak, bir servisin belirli bir ihtiyacı gerçekten karşılayıp karşılamadığını deneyerek
kontrol eden bir test ve kanıt servisidir. Kendi altyapınızda çalışır. Geliştiriciler ve
yapay zekâ ajanları, bir servisi veya aracı kullanmaya karar verirken bu sonuçlardan yararlanır.

Desteklenen bir koşulu, küçük bir test verisini ve aday servisleri verirsiniz. Proofbeak aynı
denemeyi her adayda çalıştırır, oluşan durumu okur ve her servis için sonucu gerekçesiyle döndürür.
Ajanlar MCP veya REST üzerinden kullanabilir; geliştirici de komut satırından çalıştırabilir.

## Somut bir örnek

Dosya yükleyen bir ajan geliştiriyorsunuz. Ajanın aynı dosyayı birden fazla kez saklaması
istenebilir. Şu davranışı istiyorsunuz:

> Aynı içerik aynı anahtara tekrar yazıldığında tek bir test verisi nesnesi kalsın;
> anahtarı ve içeriği değişmesin.

Bu koşulu WsgiDAV ve Apache WebDAV gibi iki depolama servisinde denemek istiyorsunuz.
Proofbeak her aday için:

1. Geçici bir test koleksiyonu oluşturur ve içine test dosyasını yazar.
2. Saklanan veriyi listeler ve okuyarak başlangıç durumunu kaydeder.
3. Aynı içeriği aynı anahtara tekrar yazar.
4. Veriyi yeniden listeler ve okur; nesne sayısını, anahtarı ve içerik hash'lerini kontrol eder.
5. Oluşturduğu test kaynaklarını temizlemeyi dener; temizliğin durumunu ayrıca raporlar.

Sonuç bu okumalara dayanır. Dosya yükleme isteğine başarılı yanıt gelmesi, saklanan verinin
beklediğiniz koşulu sağladığını tek başına göstermez.

Mevcut gerçek servis entegrasyon testinde iki WebDAV servisi de bu koşulu geçiyor. Bu sonuç,
kaydedilen veri ve yapılandırmadaki davranışı gösterir; servisler arasında genel bir sıralama,
eşzamanlı yazma güvenliği veya çökme sonrası dayanıklılık iddiası oluşturmaz.
[Ayrıntılı depolama örneği](docs/storage-comparison.md).

## Ne verirsiniz, ne alırsınız?

| Girdi | Örnek |
| --- | --- |
| Kayıtlı bir test paketinden koşul | Tekrar yazmadan sonra nesne sayısı, anahtar ve içerik korunsun |
| Küçük, geçici test verisi | `probe.txt` ve seçtiğiniz içerik |
| Yapılandırılmış aday servis kimlikleri | `webdav:wsgidav`, `webdav:apache` |

Her adayın sonucu ayrı döner:

| Sonuç | Anlamı |
| --- | --- |
| `pass` | Bu denemede istenen koşulların tamamı gözlendi |
| `fail` | Gözlenen bir değer istenen koşulu ihlal etti |
| `inconclusive` | Yetki, belirsiz işlem sonucu veya eksik gözlem nedeniyle karar verilemedi |

JSON ve okunabilir HTML raporları; kontrolleri, beklenen/gözlenen değerleri, önce/sonra
kayıtlarını, zaman damgalarını, kanıt hash'ini, test/adaptör sürümlerini ve temizlik durumunu içerir.
Test başarılı olsa da temizlik tamamlanamamış olabilir; rapor iki bilgiyi ayrı gösterir.

Geliştirici veya dışarıdaki ajan, servis seçimini ya da entegrasyon kararını bu kayıtları
inceleyerek verir. Proofbeak gözlenen davranışı ve kanıtını sunar.

## Kimler için?

- **Ajan uygulaması geliştirenler:** Ajanın kullanacağı aracın ihtiyaç duyulan davranışını doğrulamak için.
- **Backend ve platform ekipleri:** Uyumlu servisleri aynı kabul koşuluyla karşılaştırmak için.
- **Entegrasyon geliştiricileri:** Somut bir ihtiyacı tekrar çalıştırılabilir test ve incelenebilir rapora dönüştürmek için.

## Bir ajan nasıl kullanır?

Operatör izin verilen test servislerini yapılandırır; sağlayıcı kimlik bilgileri sunucuda kalır.
Ajan kullanılabilir test paketlerini keşfeder, koşulu ve adayları gönderir, işlemin bitmesini
bekler, karşılaştırmayı ve kanıtları MCP veya REST üzerinden okur.

Aynı istek ve anahtarla tekrar çağrı yapıldığında eski run döner. Uygun geçmiş kanıtlar aynı
bağlamda açıkça aranıp kullanılabilir. Bir kanıt okuma isteği arka planda yeni test başlatmaz.
Ajanın veya modelin çalıştığı ortam sizin kontrolünüzde kalır.

[MCP kullanım rehberi](docs/mcp-walkthrough.md), iki gerçek servisle keşif, test, kanıt
doğrulama ve rapor dışa aktarma akışını çalıştırılabilir komutlarla gösterir.

## Bugün neleri destekliyor?

**0.4.0** sürümünde iki kayıtlı test paketi var:

| Paket | Kontrol edilen davranış | Adaptörler |
| --- | --- | --- |
| `storage.put_same_key.v1` | Tekrar yazmada nesne sayısı, anahtar ve içeriğin korunması | Yerel filesystem referansı; WebDAV üzerinden WsgiDAV ve Apache |
| `calendar.move_one.v1` | Tekrarlayan bir etkinliğin bir oluşumunu taşırken diğer oluşumları ve sürelerini korumak | Gerçek Radicale entegrasyonu; protokol testleri olan, canlı doğrulaması henüz yapılmamış isteğe bağlı Google adaptörü |

Depolama, ürünün ilk kullanım örneği. Test motoru, kanıt yaşam döngüsü ve ajan arayüzleri
paketler arasında ortak. Yeni bir koşul veya servis için geliştiricinin güvenilir bir test
paketi ve/veya adaptör ekleyip doğrulaması gerekir. İstekler kapalı JSON tanımları kullanır;
bu sürüm rastgele doğal dil taleplerini otomatik teste çevirmez veya rastgele kod çalıştırmaz.
[Paket ve adaptör ekleme sözleşmesi](docs/trial-packs.md).

## Yerelde deneyin

Repo kökünden, Linux/macOS ve Python 3.11+ ile gerçek bir filesystem testi çalıştırabilirsiniz.
Bu örnek ek Python paketi, bulut hesabı, model servisi veya Docker gerektirmez:

```bash
PYTHONPATH=src python3 -m proofbeak run examples/storage.put_same_key.v1.json \
  --candidate filesystem --key first-trial \
  --output reports/first-trial.json --html reports/first-trial.html
```

`reports/first-trial.html` dosyasını açarak koşulu, gerçek gözlemleri ve temizliği inceleyin.
Başarılı bir çalıştırmada komut çıktısında şu satır görünür:

```text
filesystem: pass (conditions_met); cleanup=cleaned
```

Bu deneme yerel filesystem adaptörünü kontrol eder. İki gerçek HTTP servisini karşılaştırmak
sıradaki adımdır: [WebDAV kurulumu](docs/storage-comparison.md) veya
[MCP rehberi](docs/mcp-walkthrough.md). Dış sağlayıcılarda ayrı test hesapları/alanları kullanın.
Örnekler sentetik veri kullanır.

## Kurulum ve sürüm kapsamı

Kararlı sürüm, SQLite durumunu yöneten kontrollü **tek koordinatörlü** kurulum içindir.
Doğrudan Python, konteyner veya standart Kubernetes/Helm ile çalışabilir. OpenShift isteğe
bağlıdır. Çekirdek için belirli bir bulut, Google hesabı veya LLM gerekmez; yalnızca seçtiğiniz
entegrasyonlar için ilgili sağlayıcının kimlik bilgileri gerekir.

Serviste tenant'a bağlı API anahtarları, kalıcı kuyruk, sınırlandırılmış worker'lar, açıkça
başlatılan kaynak temizleme/kurtarma işlemleri, kanıt geçerliliği/iptali/saklama politikası ve doğrulanmış
yedekleme/geri yükleme bulunur. CLI veritabanı ve raporları yalnızca sahibi okuyabilir;
rapor değişimi atomiktir. Uzaktan SSO, çoklu veri merkezi ve ortak kanıt kataloğu bu sürümün dışındadır.

Yeterlilik testleri gerçek sağlayıcıları, MCP/REST'i, 0.3.0'dan yükseltmeyi, tekrarlı istekleri,
yeniden başlatma ve geri yüklemeyi, konteynerleri ve Kubernetes ağ kurallarını kapsar.
Bunlar sınırlı sentetik testlerdir; gerçek kullanıcı benimsemesi ve ajan karar kalitesindeki
artış henüz ölçülmemiştir. [Kararlılık kapsamı](docs/stability.md) · [Sürüm notları](CHANGELOG.md).

## Rehberler

| Yapmak istediğim | Rehber |
| --- | --- |
| İki depolama servisini karşılaştırmak | [Depolama testi](docs/storage-comparison.md) |
| Ajan bağlayıp MCP akışını çalıştırmak | [MCP kullanımı](docs/mcp-walkthrough.md) |
| REST, tenant ve tool sözleşmelerini incelemek | [Ajan arayüzleri](docs/agent-interfaces.md) |
| CLI hata kontrollerini, takvim örneğini veya temizliği çalıştırmak | [CLI rehberi](docs/cli-guide.md) |
| Yeni koşul veya sağlayıcı eklemek | [Test paketleri ve adaptörler](docs/trial-packs.md) |
| Kanıtı yeniden kullanmak, süresini veya silinmesini yönetmek | [Kanıt yaşam döngüsü](docs/evidence-lifecycle.md) |
| Yapılandırmayı kontrol etmek, yükseltmek, yedeklemek veya geri yüklemek | [Durum kurtarma](docs/state-recovery.md) |
| Konteyner veya Helm ile kurmak | [Dağıtım](docs/deployment.md) |
| Tasarımı ve worker sınırlarını anlamak | [Mimari](docs/architecture.md) · [Worker'lar](docs/worker-processes.md) |
| Geliştirme ve kullanıcı pilotunu incelemek | [Yol haritası](docs/roadmap.md) · [Pilot](docs/pilot.md) |

## Geliştirme

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

[Katkı kuralları](CONTRIBUTING.md) · [Apache-2.0 lisansı](LICENSE).
