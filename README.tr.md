# Proofbeak

**Bir servisin ihtiyacınızı karşıladığını test edin. Kanıta dayanarak karar verin.**

[English](README.md) · [Türkçe](README.tr.md)

Proofbeak, geliştiriciler ve yapay zekâ ajanları için kendi altyapınızda çalışan bir kabul
testi ve kanıt servisidir. Uygulamanızın ihtiyaç duyduğu davranışı tanımlar, aday servisleri
seçer ve aynı senaryoyu her birinde çalıştırırsınız. Proofbeak gözlediği sonuçları sunar;
servis seçimi veya entegrasyon kararınızı bu kanıtlara dayanarak verirsiniz.

Mevcut bağlantıların sunduğu işlemleri, test verilerini ve kabul koşullarını JSON ile
birleştirerek kendi senaryolarınızı yazarsınız. Repodaki örnekleri ihtiyacınıza göre
uyarlayabilirsiniz.

## Nasıl çalışır?

1. **Tanımlayın:** Hazırlığı, işlemleri, gözlemleri ve beklediğiniz sonuçları belirleyin.
2. **Seçin:** Yapılandırılmış adaylar arasından senaryoyla uyumlu servisleri seçin.
3. **Doğrulayın:** Sağlayıcıda işlem başlatmadan önce tanımı kontrol edin.
4. **Çalıştırın:** Denemeyi uygulayın ve sonucunu bağımsız okumalarla kontrol edin.
5. **İnceleyin:** Karşılaştırmayı, kanıtları ve ayrı raporlanan temizlik durumunu görün.

| Sonuç | Anlamı |
| --- | --- |
| `pass` | Gözlemler istenen koşulları sağlıyor. |
| `fail` | Gözlenen bir değer istenen koşulu ihlal ediyor. |
| `inconclusive` | Eksik gözlem veya operasyonel sorun nedeniyle karar verilemiyor. |

JSON ve HTML raporlarında beklenen ve gözlenen değerler, önce/sonra kayıtları ve kanıtın
kaynağı bulunur. Son seçimi geliştirici veya dışarıdaki ajan yapar.

## Hızlı başlangıç

Repo kökünden, Linux/macOS üzerinde Python 3.11+ kullanın. Bu yerel örnek ek Python paketi,
harici hesap veya model servisi gerektirmez:

```bash
PYTHONPATH=src python3 -m proofbeak validate examples/scenario.delete_and_preserve.json \
  --candidate scenario:filesystem
PYTHONPATH=src python3 -m proofbeak run examples/scenario.delete_and_preserve.json \
  --candidate scenario:filesystem --key first-trial \
  --output reports/first-trial.json --html reports/first-trial.html
```

Sonucu görmek için `reports/first-trial.html` dosyasını açın. Örnek, geçici dosyalar kullanır.
JSON dosyasını kopyalayıp kendi koşulunuza göre düzenleyin; değişen tanım için yeni anahtar
kullanın. Aynı istek ve anahtarla tekrar çağrı, özgün çalıştırmayı getirir.

Kendi tanımınızı yazıp birden fazla yapılandırılmış servise uygulamak için
[senaryo rehberini](docs/scenarios.md) izleyin.

## Arayüzler ve kapsam

Proofbeak'i **CLI, REST veya MCP** üzerinden kullanabilirsiniz. Sağlayıcı bağlantıları ve
kimlik bilgileri operatör tarafından yapılandırılır; ajanınızın çalıştığı ortam sizin
kontrolünüzde kalır.

Kullanıcı tanımlı senaryolar şu anda nesne yazma/okuma/listeleme/silme işlemlerini ve
yapılandırılmış HTTP JSON okumalarını destekler. Bu işlemleri birleştirmek için JSON tanımı
yeterlidir. Yeni bir protokol veya işlem için güvenilir bir bağlantı geliştirmek gerekir.

Doğrudan Python, konteyner veya standart Kubernetes/Helm ile çalışır. Mevcut kurulum kapsamı,
SQLite durumu üzerinde tek koordinatör kullanır. Yapılandırma ve işletim sınırları için
dağıtım rehberine bakın.

## Belgeler

- [Senaryo yazımı ve örnekler](docs/scenarios.md)
- [REST ve MCP entegrasyonu](docs/agent-interfaces.md)
- [Bağlantılar ve genişletme sözleşmesi](docs/trial-packs.md)
- [Kanıt geçmişi ve yeniden kullanım](docs/evidence-lifecycle.md)
- [Dağıtım](docs/deployment.md) · [Yedekleme ve kurtarma](docs/state-recovery.md)
- [Gerçekleştirilen testler](docs/acceptance-results.tr.md) · [Sürüm notları](CHANGELOG.md)

[Katkıda bulunma](CONTRIBUTING.md) · [Mimari](docs/architecture.md) · [Yol haritası](docs/roadmap.md) · [Apache-2.0](LICENSE)
