# Proofbeak

**Bir servisin ihtiyacınızı karşıladığını test edin. Kanıta dayanarak karar verin.**

[English](https://github.com/eturkok/proofbeak/blob/main/README.md) · [Türkçe](https://github.com/eturkok/proofbeak/blob/main/README.tr.md)

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
PYTHONPATH=src python3 -m proofbeak example --output scenario.json
PYTHONPATH=src python3 -m proofbeak validate scenario.json \
  --candidate scenario:filesystem
PYTHONPATH=src python3 -m proofbeak run scenario.json \
  --candidate scenario:filesystem --key first-trial \
  --output reports/first-trial.json --html reports/first-trial.html
```

Sonucu görmek için `reports/first-trial.html` dosyasını açın. Örnek, geçici dosyalar kullanır.
JSON dosyasını kopyalayıp kendi koşulunuza göre düzenleyin; değişen tanım için yeni anahtar
kullanın. Aynı istek ve anahtarla tekrar çağrı, özgün çalıştırmayı getirir.

Kendi tanımınızı yazıp birden fazla yapılandırılmış servise uygulamak için
[senaryo rehberini](https://github.com/eturkok/proofbeak/blob/main/docs/scenarios.md) izleyin.

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

- [Kurulum ve paket dosyaları](https://github.com/eturkok/proofbeak/blob/main/docs/installation.md)
- [Senaryo yazımı ve örnekler](https://github.com/eturkok/proofbeak/blob/main/docs/scenarios.md)
- [REST ve MCP entegrasyonu](https://github.com/eturkok/proofbeak/blob/main/docs/agent-interfaces.md)
- [Bağlantılar ve genişletme sözleşmesi](https://github.com/eturkok/proofbeak/blob/main/docs/trial-packs.md)
- [Kanıt geçmişi ve yeniden kullanım](https://github.com/eturkok/proofbeak/blob/main/docs/evidence-lifecycle.md)
- [Dağıtım](https://github.com/eturkok/proofbeak/blob/main/docs/deployment.md) · [Yedekleme ve kurtarma](https://github.com/eturkok/proofbeak/blob/main/docs/state-recovery.md)
- [Gerçekleştirilen testler](https://github.com/eturkok/proofbeak/blob/main/docs/acceptance-results.tr.md) · [Sürüm notları](https://github.com/eturkok/proofbeak/blob/main/CHANGELOG.md)

[Katkıda bulunma](https://github.com/eturkok/proofbeak/blob/main/CONTRIBUTING.md) · [Mimari](https://github.com/eturkok/proofbeak/blob/main/docs/architecture.md) · [Yol haritası](https://github.com/eturkok/proofbeak/blob/main/docs/roadmap.md) · [Apache-2.0](https://github.com/eturkok/proofbeak/blob/main/LICENSE)
