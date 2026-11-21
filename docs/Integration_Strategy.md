# استراتيجية التكامل وإدارة الطوابير والدفعات (Integration & Queue Strategy)
## استوديو الإنتاج المرئي بالذكاء الاصطناعي | Universal AI Video Production Studio
**تاريخ الوثيقة:** 16 نوفمبر 2026  
**الحالة:** مواصفة هندسية محدثة ومدققة للمرحلة الثانية  

---

## 1. بنية التجريد للمزودات متعددة الوسائط (Multi-Modal Provider Abstraction)

تعتمد المنصة على نمط الواجهات المعيارية (Interface-Based Adapters) لربط كافة خدمات الذكاء الاصطناعي ومصادر الوسائط دون تقييد الكود بأي مزود واحد:

```mermaid
graph TD
    subgraph Execution & Service Layer
        GUI[Minimal Local GUI]
        ServiceLayer[Documented Service Layer / API Controller]
        CLI[Headless CLI Core Engine]
    end

    subgraph Core Abstraction Interfaces
        LLM_I[BaseLLMProvider]
        Img_I[BaseImageProvider]
        Vid_I[BaseVideoProvider]
        Aud_I[BaseAudioProvider]
        Ingest_I[BaseMediaIngestProvider]
    end

    subgraph Pluggable Adapters
        Vid_I --> Kling[Kling AI Adapter: T2V / I2V]
        Vid_I --> Hailuo[Hailuo Minimax Adapter: I2V]
        Vid_I --> Luma[Luma Dream Machine: T2V / I2V]
        Vid_I --> LocalWan[Wan 2.1 Local Adapter]
        
        Img_I --> Flux[FLUX.1 / Fal.ai Adapter]
        Img_I --> Midjourney[Midjourney API Adapter]
        
        Aud_I --> Eleven[ElevenLabs Voice Adapter]
        Aud_I --> OpenAITTS[OpenAI TTS Adapter]
        Aud_I --> EdgeTTS[EdgeTTS Free Local Adapter]
        
        Ingest_I --> LocalVault[Local Media & B-Roll Vault]
    end

    GUI --> ServiceLayer
    CLI --> ServiceLayer
    ServiceLayer --> Core Abstraction Interfaces
```

---

## 2. معمارية طابور المهام المحلي بـ SQLite ومحدداته الصارمة (SQLite Persistent Queue)

لضمان إدارة مستقرة للدفعات في مرحلة MVP دون تعقيدات سيرفرات سحابية خارجية، يعتمد النظام على طابور مهام محلي ومستمر في قاعدة بيانات SQLite.

### أ. الآليات الهندسية لطابور SQLite في MVP:
1. **تفعيل وضع الكتابة المسبقة (WAL Mode):** تشغيل `PRAGMA journal_mode = WAL;` و `PRAGMA synchronous = NORMAL;` مما يسمح بالقراءة المتزامنة من عدة خيوط مع تقليل زمن قفل الكتابة.
2. **ضبط مهلة الانتظار (Busy Timeout):** تعيين `busy_timeout = 10000;` (10 ثوانٍ) لمنح العمليات المتزامنة وقتاً كافياً للحصول على قفل الكتابة قبل إلقاء خطأ `SQLITE_BUSY`.
3. **منع تكرار المهام (Task Idempotency):**
   * توليد بصمة فريدة `idempotency_key` مبنية على تجزئة SHA-256 للمدخلات (المشهد، البرومبت، إعدادات النموذج).
   * التحقق من السجل قبل إرسال أي طلب خارجي؛ في حال وجود أصل مكتمل بنفس المفتاح، يتم ربطه فوراً وتخطي الاستدعاء لحماية الميزانية من الهدر.
4. **استئناف المهام والتعافي التلقائي (Resumable Pipeline):**
   * تسجيل حالة كل مشهد بدقة (`pending`, `generating_asset`, `rendering`, `completed`, `failed`).
   * عند حدوث انقطاع في الكهرباء أو تعطل البرنامج، يقرأ النظام حالة المشروعات غير المكتملة ويستأنف العمل من المشهد الأخير غير المنجز.

### ب. محددات SQLite ومعايير الانتقال لقاعدة بيانات وطابور موزع (Data-Driven Migration Triggers):
تعتمد المنصة قاعدة صارمة: **لا يتم الانتقال من SQLite إلى محرك موزع استناداً لتخمينات نظرية، بل بناءً على اختبارات الضغط الفعلية (Stress Testing) ومراقبة مؤشرات الأداء التالية**:

1. **معدل أخطاء انشغال القاعدة (`SQLITE_BUSY` Error Frequency):**
   * إذا تجاوز معدل حدوث خطأ `SQLITE_BUSY` نسبة **2%** من إجمالي العمليات رغم تطبيق مهلة الـ 10 ثوانٍ ووضع WAL.
2. **زمن انتظار قفل قاعدة البيانات (Database Lock Wait Latency):**
   * إذا ارتفع متوسط زمن الانتظار للحصول على قفل الكتابة (Lock Latency) ليتجاوز **500 مللي ثانية** للمعاملة الواحدة تحت ضغط التشغيل بالدفعات.
3. **معدل معالجة المهام الإجمالي (Task Throughput Degradation):**
   * حدوث هبوط ملحوظ في معدل إنجاز المهام في الدقيقة مقارنة بقدرة الـ APIs الخارجية والعتاد المحلي.
4. **زمن استجابة النظام العام (System Response Latency):**
   * ارتفاع زمن استجابة استعلامات القراءة والكتابة الموجهة لطبقة الخدمات فوق **1.5 ثانية**.
5. **عمق الطابور ومعدل نموه (Queue Depth & Accumulation Rate):**
   * تراكم المهام بمعدل نمو أسرع من قدرة المعالجة المحلية، مع تجاوز حجم الطابور النشط حاجز **5,000 إلى 10,000 مهمة**.
6. **الحاجة المادية لتوزيع الخوادم (Physical Distributed Nodes Requirement):**
   * متى ما تطلبت خطة الإنتاج تشغيل عمال معالجة ورندر (Worker Nodes) عبر **أجهزة أو خوادم سحابية متعددة منفصلة عبر الشبكة**، حيث يتعذر مشاركة ملف SQLite بأمان عبر الشبكة، مما يستلزم الانتقال لمحرك موزع متقدم مثل (**PostgreSQL + Redis/Celery**).

---

## 3. استراتيجية التحويل عند الأعطال وتتبع التكلفة (Failover & Cost Auditing)

1. **التحويل التلقائي عند الأعطال (Fallback Routing):**
   * عند تلقي رمز خطأ في الشبكة أو تجاوز حد الاستهلاك (HTTP 429 / 503) من مزود الفيديو الأساسي، يقوم النظام تلقائياً بتحويل المهمة إلى المزود الاحتياطي المعرف في القالب.
2. **سجل التكلفة واستهلاك الموارد (Granular Cost Accounting):**
   * تسجيل كل استهلاك (عدد الـ Tokens في LLM، ثواني التوليد في نماذج الفيديو، وعدد الحروف في TTS) وحفظها في جدول `task_costs` لإظهار التكلفة الفعلية لكل دقيقة منتجة في الواجهة الرسومية.
