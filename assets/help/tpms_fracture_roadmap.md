# TPMS kırılma ve çökme yol haritası

Bu planı RustMech’te **Mekanik → Peridinamik → Kırılma yöntemleri planı** üzerinden açabilirsiniz.

## İlk teslim · Peridinamik basma

Gyroid, Diamond ve RustMech’teki diğer analitik TPMS geometrileri iki sürtünmesiz plaka arasında deplasman kontrollü olarak basılır. 3B nokta haritası, her parçacıkta kopan bağların ağırlıklı oranını gösterir. Kuvvet–deplasman grafiği ve katman hasarı grafiği aynı yük adımına bağlıdır. Katmanlar numunenin başlangıç yüksekliğine ve TPMS periyoduna göre tanımlanır. Her iki grafik için sonuç klasöründe CSV bulunur.

İlk model izotropik, bağ tabanlı ve gevrektir. Üç boyutlu sürekli ortam modelinin Poisson oranı 0,25’tir. Kritik bağ uzaması malzeme için kalibre edilmelidir. Numune büyüklüğü ve katı oranı aynı kalsa bile parçacık aralığı ve etkileşim ufku ince cidarları ve kırılma yükünü etkileyebilir. Kırılan parçalar arasındaki kısa menzilli itme ve plakalarla sürtünmesiz temas yaklaşık ceza potansiyelleriyle hesaplanır. Son adımda denge veya bağ kopması yakınsamazsa sonuç kısmi olarak işaretlenir.

İlk deneysel doğrulama için tek malzeme ve üretim yönüyle basılmış Gyroid ve Diamond numunelerinde kuvvet–yer değiştirme eğrisini, ilk kırılma yerini ve hasar sırasını karşılaştır. Aynı numuneyi en az üç parçacık çözünürlüğünde ve birden fazla ufukta çalıştır. Deneysel saçılım ve kabul toleranslarını ölçülen veriden belirle. [TPMS basma deneyleri](https://pmc.ncbi.nlm.nih.gov/articles/PMC9654767/) · [PMB ve kısa menzilli temas](https://docs.lammps.org/stable/Howto_peri.html)

## Sonraki analiz kapsamı

MPM çalışma alanında sürtünmesiz 3B TPMS basma kullanılabilir. Aşağıdaki MPM satırı temas ve kırılma kapsamının genişletilmesini ele alır.

| Yöntem | TPMS için kullanım | Bir sonraki doğrulama |
| --- | --- | --- |
| Durum tabanlı peridinamik | Malzemenin ölçülen Poisson oranı, plastisite ve sünek kırılma | Katı çekme numunesinde elastik/plastik eğri ve gevrek TPMS referansında ilk kırılma yükü |
| Faz alanı kırılması ve sonlu elemanlar | İnce cidarlarda gevrek çatlak başlangıcı, dallanma ve karşılaştırmalı kırılma enerjisi | Çentikli referans ve TPMS basmada ağ ile düzenlileştirme uzunluğu duyarlılığı |
| Kohezif bölge modeli veya XFEM | Katmanlar arası ayrılma, baskı yönü kaynaklı zayıf düzlemler ve mevcut çentikten çatlak yayılımı | Baskı yönlerine göre arayüz dayanımı, ayrılma enerjisi ve çatlak yolu |
| MPM’de sürtünmeli temas ve çökme | Büyük şekil değiştirme, sürtünmeli temas ve kırılan hücrelerin sıkışması | Tek TPMS hücresinde yük–yer değiştirme, temas ve enerji dengesi |
| Geometrik ve malzeme doğrusal olmayan sonlu elemanlar | Duvar burkulması, plastik mafsallaşma ve çatlak oluşmadan önceki yük düşüşü | Aynı TPMS numunesinde deneysel yük–yer değiştirme ve burkulma modu |

[Durum tabanlı peridinamik modeller](https://docs.lammps.org/bond_bpm_peri.html) · [Faz alanı kırılmasının deneysel doğrulaması](https://www.sciencedirect.com/science/article/abs/pii/S004578251400423X) · [Kohezif ve XFEM karşılaştırması](https://www.sciencedirect.com/science/article/pii/S0997753815000157)

Peridigm veya LAMMPS aynı gevrek numunede bağımsız çözücü karşılaştırması için kullanılabilir. Kuvvet, temas ve ayrıklaştırma ayarları karşılaştırılabilir hale getirildikten sonra yeni çözücüye geçişi ölç. [Peridigm](https://github.com/peridigm/peridigm) · [LAMMPS PERI](https://docs.lammps.org/stable/Howto_peri.html)

Her yeni RustMech analizinin TRUBA yetenek kontrolü, Slurm iş takibi ve sonuç indirme adaptörü olmalı. Yöntemleri aynı geometri, malzeme verisi ve plaka koşullarıyla karşılaştır.
