# وثيقة التصميم المعماري للمهمة 3.5: طابور المهام المستمر وآلية الاستئناف (النسخة المعتمدة النهائية الشاملة)
# Task 3.5 Architecture Design: Persistent Task Queue & Crash Resumption (Final Comprehensive Specification)

**تاريخ الوثيقة:** 16 نوفمبر 2026  
**المشروع:** Universal AI Video Production Studio  
**المرحلة:** المرحلة الثالثة — تطوير النواة البرمجية والتكامل (Phase 3: Core Engine Development & Integration)  
**المهمة المستهدفة:** Task 3.5 (Persistent Task Queue & Resumption) — المرحلة الأولى: حسم التصميم المعماري  
**الحالة:** 🟡 **تصميم معماري نهائي مكتمل ومدقق هندسياً — بانتظار الاعتماد الصريح للانتقال للتنفيذ البرمجي (Read-Only Design Phase)**  

---

## 1. الأهداف والنطاق الدقيق للمهمة 3.5 (Executive Summary & Scope)

تهدف المهمة 3.5 إلى تأسيس **طابور المهام المستمر (Persistent Task Queue)** ومحرك معالجة خلفي قوي (Background Worker Engine) داخل النواة الصامتة للمنصة، محققاً:

1. **الاستمرارية بعد الانهيار وإعادة التشغيل (Persistence & Crash Survivability):**
   - حفظ كامل حالة المهام وسجل خطواتها في قاعدة بيانات SQLite عبر وضع WAL، بحيث لا يؤدي انقطاع التيار الكهربائي أو إغلاق التطبيق المفاجئ إلى فقدان أي مهمة.
2. **إلغاء الادعاءات المطلقة حول Idempotency المزودين والتعامل الصارم مع النتيجة المجهولة (Provider Idempotency Limits & Unknown Outcomes):**
   - إزالة أي ادعاء بأن تكرار `client_request_id` يعني تكلفة صفرية ($0)؛ وتوثيق تباين المزودين في صلاحية المفاتيح، والتعامل مع أي حالة غير مؤكدة كـ **`UNKNOWN_EXTERNAL_STATE`** تستلزم المصالحة أو قرار المشغل لمنع التكلفة المكررة.
3. **التعافي الآمن للأصول وخطة العزل دون حذف الأصول السليمة (Safe Recovery of Original Assets & Quarantine):**
   - حظر حذف أي ملف من `media_vault/originals/` لمجرد اختلاف بصمة التجزئة SHA-256، والتحقق الصارم من ملكية الملف وربطه بسجل `file_operations` و `media_assets` والمراجع النشطة، ونقل الملفات المشبوهة أو المتضاربة إلى مسار العزل الآمن (`quarantine/`) دون المساس بالأصول السليمة.
4. **تسجيل البصمة المتوقعة مسبقاً وتكامل `file_operations` القائم:**
   - تسجيل بصمة SHA-256 المتوقعة في جدول `file_operations` قبل تنفيذ النقل الذري `os.replace`، واعتماد جدول `file_operations` المؤسس في المهمة 3.4 كـ Single Source of Truth للعمليات الفيزيائية.
5. **الدلالات الكاملة لترويسة `Retry-After` وفحص TTL الشامل (Complete Retry-After Semantics):**
   - دعم صيغتي الثواني (Delta-seconds) وتاريخ HTTP (HTTP-date)، ومعالجة انحراف الساعة (Clock Skew) والتواريخ غير الصالحة، وحظر إعادة الإرسال قبل موعد المزود، وتطبيق فحص مهلة الصلاحية القصوى للمهمة (Task TTL = 24h) على كافة أنواع التأخير.
6. **دورة حياة حيازة القناة الذرية في SQLite وضمانات Fencing Token (Atomic Channel Lease Lifecycle):**
   - تحرير الحيازة فوراً عند الانتقال إلى حالات عدم التشغيل (`RETRY_PENDING`, `RETRY_PENDING_APPROVAL`, `FAILED`, `CANCELLED`, `COMPLETED`).
   - فرض مطابقة المفاتيح الأربعة (`channel_id`, `task_id`, `worker_id`, `execution_token`) عند تجديد الحيازة في النبض أو تحريرها لمنع `TaskReaper` من المساس بحيازة عامل أحدث، وضمان أن تحديثات المهام والحيازات تتم داخل معاملات SQLite ذرية فورية `BEGIN IMMEDIATE`.
7. **اتساق مخطط الاعتماديات والمهام التابعة (DAG Consistency & Kahn's Cycle Detection):**
   - حصر الاعتماديات داخل نفس القناة، ثبات الشجرة، والتحقق الذري من الحلقات التكرارية بخوارزمية كان، ومعالجة الإلغاء والفشل التتالي.
8. **الفصل الصارم لأدلة الاختبارات (Test Evidence Classification):**
   - التمييز الصريح بين الاختبارات الـ 49 المكتملة للمهام السابقة ومصفوفة القبول المخططة للمهمة 3.5 (18 سيناريو) التي لم تُنفذ بعد في هذه المرحلة التصميمية.

---

## 2. مطابقة القرارات المعمارية المعتمدة (Architectural Compliance)

| القرار المعماري | مقتضى القرار | كيفية تحقيقه في تصميم طابور المهام (Task 3.5) |
| :--- | :--- | :--- |
| **ADR-001: Headless Core & Service Layer** | عزل النواة ومنطق الأعمال عن واجهات المستخدم. | بناء `TaskService` و `TaskQueue` و `WorkerPool` كطبقة خدمات مستقلة بالكامل تعمل في الخلفية بدون أي اعتمادية على واجهات GUI. |
| **ADR-005: Strict Multi-Channel Isolation** | عزل بيانات القنوات ومنع التداخل أو التسريب. | كل مهمة تنتمي وجوباً إلى `channel_id` محدد، ويحظر إنشاء أي اعتماديات DAG عبر القنوات، مع عزل مسارات العمليات الفيزيائية للملفات. |
| **ADR-007: SQLite Persistent Queue & Triggers** | اعتماد طابور SQLite بوضع WAL ومهلة 10 ثوانٍ ومفاتيح Idempotency، مع ربط الانتقال لطابور موزع باختبارات الضغط. | تنفيذ الطابور في SQLite محلياً، مع حظر المعاملات الطويلة، وفحص معايير الترقية (`SQLITE_BUSY > 2%`، انتظار القفل > 500ms، عمق الطابور > 5000 مهمة) كقرار معماري صريح يستلزم موافقة منفصلة. |
| **ADR-008: Strict Export Gate & Audit Logging** | حظر التصدير دون ترخيص، وتوثيق العمليات في سجل التدقيق غير القابل للتلاعب. | المهام التي تتضمن استيراد أو تصدير وسائط تستدعي `LicenseService.check_export_eligibility`، وتوثق نتائجها عبر `AuditService` برقم تسلسلي وسلسلة تجزئة. |
| **Task 3.4: Crash Recovery Journal** | تكامل العمليات الفيزيائية للملفات مع قاعدة البيانات. | خطوات المهمة التي تنشئ ملفات وسائط تستخدم جدول `file_operations` القائم لتفادي ازدواجية السجلات وضمان التعافي الفيزيائي المتسق. |

---

## 3. حدود Idempotency لدى المزودين الخارجيين والتعامل مع النتيجة المجهولة
### (Provider Idempotency Guarantees & Unknown Outcome Protocol)

### 3.1. إزالة الادعاء العام بالتكلفة الصفرية (Removal of Universal Zero-Cost Guarantee):
> [!WARNING]
> **تصحيح معماري ملزم:**  
> لا يوجد ضمان عام أو شامل بأن إعادة إرسال طلب بنفس المعرف `client_request_id` يعني حتماً تكلفة صفرية ($0). سلوك مزودي الذكاء الاصطناعي يتفاوت جذرياً:
> 1. **صلاحية مفاتيح Idempotency (Key TTL):** مزودون مثل OpenAI يحتفظون بمفاتيح عدم التكرار لمدة 24 ساعة فقط؛ بعد انقضاء هذه المدة، قد يُعامل الطلب كطلب جديد كلياً وتُفرض عليه تكلفة كاملة.
> 2. **مزودون بدون ضمانات صريحة (Partial / No Guarantees):** بعض مزودي توليد الفيديو (مثل واجهات Kling أو Luma غير الرسمية أو Fal.ai) قد لا تدعم مفاتيح عدم التكرار على جميع نقاط النهاية، أو تعيد إنشاء محتوى جديد عند تكرار نفس البايتات.
> 3. **فترة المعالجة الداخلية:** إذا أُعيد إرسال الطلب بينما المزود لا يزال يعالج الطلب الأول، قد يعيد المزود خطأ `HTTP 409 Conflict` أو ينشئ مهمة موازية.

### 3.2. بروتوكول التعامل الصارم مع النتيجة المجهولة (Unknown External Outcome Protocol):
عندما يتعطل التطبيق أثناء مرحلة الاتصال بالمزود الخارجي دون تسجيل نتيجة مؤكدة في قاعدة البيانات المحلية:

```
[Crash Recovery Detection]
          │
          ▼
   Check Step State:
   Was task in "DISPATCHING_EXTERNAL_JOB"?
          │
          ├── NO ──► Safe to execute step normally.
          │
          └── YES ─► [UNKNOWN EXTERNAL STATE]
                         │
                         ▼
        Does Provider contract formally guarantee
        Client Idempotency Keys with valid TTL?
                         │
        ┌────────────────┴────────────────┐
        ▼ YES                             ▼ NO
   Is Key within TTL?              Does Provider support
   ├── YES: Resend key             Querying Recent Jobs by Tag?
   │   (Log risk: $0 expected)                    │
   └── NO: Go to UNKNOWN           ┌─────────────┴─────────────┐
                                   ▼ YES                       ▼ NO
                              Query provider:             [HARD STOP - NO AUTO-RETRY]
                              Job found?                  Set status:
                               ├── YES: Save ID           `RETRY_PENDING_APPROVAL`
                               └── NO: Ambiguous state    Log Critical Audit Warning
                                                          Mandate User / Operator Action!
```

1. **التعامل مع الحالات غير المؤكدة كـ `UNKNOWN_EXTERNAL_STATE`:**
   - إذا لم يكن هناك عقد قانوني وبرمجي واضح وصريح من المزود يضمن صلاحية المفتاح وعدم الفوترة المكررة، يُصنف الطلب كـ **`UNKNOWN_EXTERNAL_STATE`**.
   - **يُحظر منعاً باتاً على النظام إعادة إرسال الطلب تلقائياً.**
2. **سلوك المهمة عند النتيجة المجهولة:**
   - تتحول المهمة فوراً إلى حالة **`RETRY_PENDING_APPROVAL`**.
   - تسجيل إشعار تدقيقي حرج في `license_audit.log`:  
     `"Uncertain external generation state detected for task {task_id} (provider: {provider_name}). Automatic retry suspended to prevent potential financial double-billing."`
   - يُلزم النظام المشغل باتخاذ إجراء صريح عبر لوحة التحكم:
     أ) إدخال معرف الوظيفة يدوياً بعد التحقق من لوحة تحكم المزود.
     ب) أو الموافقة الصريحة على إعادة إطلاق الطلب مع تحمل التكلفة المالية المحتملة.

---

## 4. عزل وتنظيف الملفات المؤقتة برمز الحيازة والجلسة
### (Safe Execution-Scoped Cleanup & Isolation)

لمنع حدوث أي تداخل بين العمال المتزامنين أو تدمير ملفات صالحة بواسطة عمال قدامى متجمدين (Zombie Workers):

### 4.1. التسمية المعزولة برمز التنفيذ (Execution-Scoped Naming):
تُسمى كافة الملفات المؤقتة المنشأة أثناء معالجة أي خطوة وفق النمط المعزول التالي:
$$\text{temp\_path} = \text{channels/}\{\text{ch}\}\text{/media\_vault/cache/task\_}\{\text{task\_id}\}\text{\_tok\_}\{\text{execution\_token}\}\text{\_}\{\text{exec\_uuid}\}\text{\_}\{\text{step\_name}\}.\text{tmp}$$
- **`task_id`:** معرف المهمة.
- **`execution_token`:** رقم الحيازة الحالي للمهمة المستخرج من SQLite.
- **`exec_uuid`:** معرف فريد عشوائي لكل جلسة تشغيل لخيط العامل لمنع تصادم الخيوط.
- **`step_name`:** اسم خطوة المعالجة.

### 4.2. قواعد العزل ومنع الحذف غير المصرح به:
1. **حصر نطاق التنظيف (Restricted Cleanup Scope):**
   - إذا استيقظ عامل قديم (`execution_token = 1`) وواجه رفض عمليته (`StaleExecutionTokenException`) لأن مهمته سُحبت لعامل جديد يحمل (`execution_token = 2`)، فإن روتين التنظيف الخاص بالعامل القديم **يُحظر عليه البحث أو الحذف بنمط عام (`glob`)**.
   - يُسمح للعامل القديم فقط بحذف المسار المحدد بالرمز الخاص به: `*tok_1_{his_uuid}*.tmp`.
   - لا يستطيع العامل القديم رؤية أو لمس ملفات العامل الجديد `*tok_2_*.tmp`.
2. **حصانة أصول الخزينة المركزية (MAM Immutability):**
   - لا يمتلك أي عامل صلاحية حذف فيزيائي مباشر للملفات داخل `media_vault/originals/`.
   - لا يتم حذف أي أصل في الخزينة إلا عبر دالة `AssetService.purge_asset_physical` التي تفحص المراجع النشطة وتتأكد من عدم ارتباط الأصل بأي مشهد أو مشروع وتتحقق من قفل القناة الحصري.

---

## 5. مصفوفة نقاط الانهيار التفصيلية والتعافي الآمن للأصول
### (Safe Recovery of Original Assets & Crash Consistency Matrix)

يعتمد النظام بالكامل على جدول **`file_operations`** المؤسس في المهمة 3.4 كـ **Single Source of Truth** للعمليات الفيزيائية للملفات دون إنشاء أي Journal مكرر.

### 5.1. التسجيل المسبق للبصمة المتوقعة والربط القطعي للعملية:
> [!IMPORTANT]
> **قاعدة التسجيل المسبق للبصمة (Pre-Replace SHA-256 Commitment):**  
> قبل استدعاء أمر النقل الذري `os.replace(temp_path, target_path)`، يُلزم النظام العامل بتسجيل صف العملية في جدول `file_operations` بحالة `FILE_WRITTEN` متضمناً بصمة التجزئة المحسوبة `file_sha256` للملف المؤقت، مع ربط العملية بمعرف حتمي:
> $$\text{file\_op\_id} = \text{"op\_" } + \text{task\_id} + \text{"\_" } + \text{step\_name} + \text{"\_tok" } + \text{execution\_token}$$

### 5.2. الحظر القطعي لحذف أصول `originals/` ومسار العزل الآمن (`quarantine/`):
- **حظر الحذف المباشر:** يُمنع منعاً باتاً حذف أي ملف موجود داخل `media_vault/originals/` لمجرد عدم تطابق بصمة التجزئة SHA-256 مع العملية الحالية؛ فالملف قد يكون أصلاً معتمداً لقناة أو مشهد آخر تم إنشاؤه مسبقاً.
- **مسار العزل الآمن (Quarantine Path):** إذا وُجد ملف في `media_vault/originals/` في مسار الهدف المتوقع للمهمة، ولكن بصمته اختلفت عن البصمة المتوقعة ولا يوجد له سجل مالك في `media_assets`، يتم نقله فوراً إلى مجلد العزل الآمن:  
  `channels/{ch}/media_vault/quarantine/{filename}.quarantine_{timestamp}`  
  مع تسجيل تحذير حرج في سجل التدقيق، وتحويل حالة العملية في `file_operations` إلى `FAILED` بسبب خطأ نزاهة (**Integrity Error**).

### 5.3. مصفوفة حالات الاستعادة وقرارات المصالحة (`reconcile_recovery`):

```
[Crash Recovery Scan on Startup]
          │
          ▼
   Inspect `file_operations` & Target File in `originals/`:
          │
   ┌──────┴───────────────────────────────────────────────────────┐
   ▼ Target File Exists in `originals/`                           ▼ Target File Missing in `originals/`
   │                                                              │
   ├─► Matches `file_operations.file_sha256`?                     ├─► Temp File exists in `cache/`?
   │     ├── YES: Adopt into `media_assets` & Checkpoint ($0)     │     ├── YES: Check SHA-256. If match -> os.replace
   │     └── NO: Check `media_assets` for ownership:              │     └── NO: Mark `FAILED`, re-execute cleanly.
   │           ├── Owned by other asset? -> COLLISION ERROR       │
   │           └── Unowned/Corrupt? -> MOVE TO QUARANTINE         │
```

| حالة السجلات في SQLite | حالة الملف على القرص | التحقق من الملكية والمراجع النشطة | القرار المعماري عند الاستئناف (`reconcile_recovery`) | النتيجة والضمانة الهندسية |
| :--- | :--- | :--- | :--- | :--- |
| **سجل `file_operations` مسجل بـ SHA-256 متوقع، ولم تُعتمد Checkpoint** | الملف موجود في `originals/` وتتطابق بصمته تماماً مع `file_sha256`. | لا يوجد تضارب مع أصل آخر في `media_assets`. | **اعتماد كامل (Safe Adoption):** يُعتمد الأصل في `media_assets`، وتُسجل نقطة الحفظ في `task_checkpoints`، وتُحدث العملية إلى `COMPLETED`. | استئناف فوري وتفادي إعادة التوليد المكلف. |
| **سجل `file_operations` مسجل بـ SHA-256 متوقع** | الملف موجود في `originals/` وتتطابق بصمته، لكنه مسجل بالفعل لأصل آخر في `media_assets`. | أصل نشط قائم يتبع نفس القناة وبنفس البصمة. | **اعتماد بالتكرار (Deduplication Reuse):** يُربط الأصل الجديد بنفس الملف الفيزيائي القائم دون كتابة مكررة، وتعتمد نقطة الحفظ. | توفير مساحة التخزين ومنع الكتابة المكررة. |
| **سجل `file_operations` مسجل ببصمة معينة** | الملف موجود في `originals/` لكن بصمته **لا تتطابق** مع البصمة المسجلة في العملية. | الملف مسجل في `media_assets` لأصل آخر وتستخدمه مشاهد نشطة. | **حظر الحذف المطلق (Preserve Asset & Fail Task):** يُمنع لمس الملف أو حذفه نهائياً؛ وتفشل عملية المهمة بـ `AssetPathCollisionException` لإنشاء مسار بديل. | حماية الأصول السليمة القائمة من التدمير. |
| **سجل `file_operations` مسجل ببصمة معينة** | الملف موجود في `originals/` لكن بصمته **لا تتطابق** مع المتوقع. | الملف غير مسجل في `media_assets` ولا توجد له أي مراجع نشطة (ملف يتيم تالف). | **عزل آمن (Safe Quarantine):** يُنقل الملف إلى `media_vault/quarantine/` دون حذفه في مكانه، وتُعلم العملية `FAILED` (خطأ نزاهة). | عدم تدمير ملفات غير معروفة وبدء الخطوة بنظافة. |
| **سجل `file_operations` بحالة `FILE_WRITTEN`** | الملف النهائي غير موجود في `originals/`، ولكن الملف المؤقت موجود في `cache/`. | فحص بصمة الملف المؤقت: متطابق مع البصمة المتوقعة. | **إكمال النقل (Finalize Move):** تنفيذ `os.replace` واعتماد الأصل و Checkpoint في معاملة واحدة. | اكتمال النقل الذري بنجاح دون إعادة طلب المزود. |
| **سجل `file_operations` بحالة `PENDING` أو `FILE_WRITTEN`** | الملف المؤقت في `cache/` تالف أو غير متطابق البصمة، والهدف غير موجود. | لا توجد أي أصول مرتبطة. | **تنظيف المؤقت (Clean Temp):** حذف الملف المؤقت التابع لجلسة الرمز `tok_1_xxx` فقط، وتعليم العملية `FAILED`، وإعادة تنفيذ الخطوة. | لا ملفات يتيمة والبدء من بيئة نظيفة. |

---

## 6. الدلالات الكاملة لترويسة `Retry-After` وفحص TTL الشامل
### (Complete Retry-After Semantics & Universal TTL Enforcement)

لحسم التفاعل الدقيق بين ترويسة المزود، والتراجع الأسي، وانحراف الساعة، ووقت صلاحية المهمة:

### 6.1. دعم صيغتي الثواني وتاريخ HTTP (Delta-seconds & HTTP-date):
يجب أن يدعم محلل استجابات المزود الصيغتين المعياريتين وفق مواصفات RFC 7231 / RFC 9110:
1. **الصيغة الرقمية بالثواني (Delta-seconds):**  
   مثل `Retry-After: 120` (الانتظار 120 ثانية).
2. **صيغة تاريخ HTTP الرسمي (HTTP-date):**  
   مثل `Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`.

### 6.2. معالجة انحراف الساعة (Clock Skew) والتواريخ غير الصالحة:
- **تحليل التاريخ (Parsing):** يتم تحليل التاريخ كطابع زمني موحد بتوقيت UTC.
- **في حال فشل التحليل أو عدم صلاحية التاريخ:** إذا كان التاريخ مشوهاً أو غير صالح، يتجاهل النظام الترويسة ويسقط تلقائياً إلى **معادلة التراجع الأسي الداخلية للنظام** مع تسجيل تحذير في السجلات.
- **معالجة انحراف الساعة (Clock Skew Resolution):**
  نحسب فارق الوقت الفعلي بين تاريخ المزود والساعة المحلية الموحدة:
  $$\Delta t = (T_{\text{http\_date}} - T_{\text{now\_utc}}).\text{total\_seconds}()$$
  - **إذا كانت $\Delta t \le 0$:** (بمعنى أن ساعة خادم المزود متأخرة قليلاً عن الساعة المحلية أو أن الوقت قد انقضى بالفعل):  
    **يُحظر الإرسال الفوري عند الصفر لتفادي الاصطدام بالمزود**؛ ويتم تطبيق مهلة أمان عشوائية تشويشية:
    $$\text{EffectiveDelay} = \text{uniform}(3,\, 7)\text{ seconds}$$
  - **إذا كانت $\Delta t > 0$:** $\text{EffectiveDelay} = \Delta t$.

### 6.3. التطبيق الشامل لفحص وقت الصلاحية القصوى (Universal Task TTL Check):
> [!IMPORTANT]
> **قاعدة فحص الصلاحية الشامل (Universal TTL Gate):**  
> يخضع **كل وقت إعادة محاولة مجدول** — سواء كان ناتجاً عن معادلة التراجع الأسي الداخلية أو عن ترويسة `Retry-After` (بالثواني أو التاريخ) — لفحص مهلة الصلاحية القصوى للمهمة:
> $$\text{ScheduledTime} = \text{now} + \text{EffectiveDelay}$$
> $$\text{TaskExpiryTime} = \text{created\_at} + 24\text{ hours (TaskTTL)}$$
> 
> **القرار:** إذا كانت $\text{ScheduledTime} > \text{TaskExpiryTime}$:  
> **تتحول المهمة فوراً ودون أي انتظار إلى حالة `FAILED`** مع تسجيل خطأ صريح:  
> `error_message = "Retry delay exceeds task maximum time-to-live (TaskTTL = 24h)"`.

### 6.4. مصفوفة حالات الجدولة وحظر الإرسال المبكر:
| نوع التأخير | القيمة المحسوبة ($\text{EffectiveDelay}$) | الشرط مع وقت الصلاحية المتبقي | الحالة الناتجة للمهمة | السلوك التشغيلي الصارم |
| :--- | :--- | :--- | :--- | :--- |
| **ترويسة المزود أو تراجع داخلي** | أي قيمة تأخير | $\text{now} + \text{Delay} > \text{TaskExpiryTime}$ | **`FAILED`** فوري | إنهاء المهمة فوراً دون إعادة محاولة، وإلغاء كافة المهام التابعة لها في شجرة الاعتماديات. |
| **ترويسة المزود `Retry-After`** | $\text{Delay} \le 1800\text{s}$ (نصف ساعة) | ضمن مهلة الصلاحية المتبقية | **`RETRY_PENDING`** | تحديد `scheduled_at = now + Delay`. تنام المهمة في SQLite، **ويُحظر قطعياً إرسال أي طلب قبل هذا الموعد**. |
| **ترويسة المزود `Retry-After`** | $\text{Delay} > 1800\text{s}$ (عدة ساعات) | ضمن مهلة الصلاحية المتبقية | **`RETRY_PENDING_APPROVAL`** | تعليق المهمة وإشعار المشغل بأن المزود يفرض تجميداً طويلاً، لمنحه خيار التحويل لمزود بديل أو انتظار الموعد. وفي جميع الأحوال **لا يُرسل أي طلب قبل انقضاء المهلة**. |
| **تراجع داخلي (غياب ترويسة المزود)** | تخضع لمعادلة التراجع الأسي مع سقف 300s | ضمن مهلة الصلاحية المتبقية | **`RETRY_PENDING`** | تحديد `scheduled_at = now + CalculatedBackoff`. |

---

## 7. دورة حياة حيازة القناة الذرية في SQLite وضمانات Fencing Token
### (Atomic Channel Lease Lifecycle & Relational Fencing)

لضمان `max_concurrent_per_channel = 1` ذرياً على مستوى قاعدة البيانات دون الاعتماد على فحوصات الذاكرة، مع الصمود التام ضد تنافس العمليات المتعددة (Multi-Process Safety):

### 7.1. متى تُحرر الحيازة في دورة حياة المهمة؟
> [!IMPORTANT]
> **قاعدة تحرير الحيازة:**  
> تُحجز الحيازة في جدول `active_channel_leases` **حصراً عندما تكون المهمة في حالة `RUNNING`**.  
> عند انتقال المهمة إلى أي من الحالات التالية، **يتم وجوباً وذرياً حذف سجل الحيازة من `active_channel_leases`**:
> 1. **`RETRY_PENDING`:** تُحرر الحيازة فوراً (المهمة نائمة تنتظر مهلة التراجع، وتحرير الحيازة يسمح للقناة بإجراء مهام صيانة أخرى أو جدولة عادلة).
> 2. **`RETRY_PENDING_APPROVAL`:** تُحرر الحيازة فوراً (المهمة معلقة بانتظار المشغل).
> 3. **`FAILED`:** تُحرر الحيازة فوراً.
> 4. **`CANCELLED`:** تُحرر الحيازة فوراً.
> 5. **`COMPLETED`:** تُحرر الحيازة فوراً.

### 7.2. اشتراط مطابقة المفاتيح الأربعة عند التجديد والتحرير ومنع Reaper من المساس بحيازة أحدث:
لمنع حدوث سباق بيانات (Race Condition) حيث يقوم المسترد `TaskReaper` بحذف حيازة عامل جديد تولى المهمة للتو:
- **قيد المطابقة الرباعي الإلزامي:**
  لا يتم تجديد أي حيازة (في نبض الحياة) ولا تحريرها (عند الانتهاء أو الاسترداد) إلا بشرط مطابقة المفاتيح الأربعة معاً:
  $$\text{MATCH KEYS: } (\text{channel\_id}, \text{task\_id}, \text{worker\_id}, \text{execution\_token})$$

1. **تجديد الحيازة في نبض الحياة (Heartbeat Pulse):**
   ```sql
   UPDATE active_channel_leases
   SET lease_expires_at = datetime('now', '+' || lease_duration_sec || ' seconds')
   WHERE channel_id = :ch
     AND task_id = :task_id
     AND worker_id = :my_worker_id
     AND execution_token = :my_token;
   ```
   إذا أعاد الاستعلام `rowcount == 0`، يدرك العامل فوراً أن حيازته سُحبت أو أُلغيت، فيُلقي فوراً `StaleExecutionTokenException` ويتوقف.
2. **استرداد المهام المنتهية الحيازة عبر المسترد (`TaskReaper`):**
   عندما يكتشف المسترد مهمة بحالة `RUNNING` انتهت حيازتها (`lease_expires_at < datetime('now')`)، يقوم داخل معاملة `BEGIN IMMEDIATE`:
   ```sql
   -- 1. حذف حيازة العامل القديم حصراً
   DELETE FROM active_channel_leases
   WHERE channel_id = :ch
     AND task_id = :task_id
     AND worker_id = :stale_worker_id
     AND execution_token = :stale_token;
   
   -- 2. تحديث المهمة وزيادة رمز الحيازة
   UPDATE tasks
   SET status = 'retry_pending',
       retry_count = retry_count + 1,
       execution_token = execution_token + 1,
       updated_at = datetime('now')
   WHERE id = :task_id
     AND execution_token = :stale_token;
   ```
   **الضمانة الهندسية:** إذا كان عامل أحدث قد استلم المهمة بالفعل برمز `execution_token` أعلى، فإن أمر `DELETE` وأمر `UPDATE` سيعيدان `rowcount == 0`، مما يمنع المسترد قطعياً من المساس بحيازة العامل الجديد!

### 7.3. سيناريو انتهاء الحيازة مع استمرار العامل القديم وصد Fencing Token:
```
[Worker A: worker_id='w1', Token=1]               [TaskReaper]                 [Worker B: worker_id='w2', Token=2]
        │                                              │                                       │
        ├── (Stalled / Network Lag)                    │                                       │
        │   Lease expires at T=60s                     │                                       │
        │                                              ▼                                       │
        │                                     Scans: Lease Expired!                            │
        │                                     - Atomic Transaction:                            │
        │                                       DELETE lease (w1, tok=1)                       │
        │                                       Task T1 -> Token=2, QUEUED                     │
        │                                              │                                       │
        │                                              └──────────────────────────────────────►│ Claims T1 (Token=2)
        │                                                                                      │ Inserts lease (w2, tok=2)
        │                                                                                      │ Starts processing...
        ▼                                                                                      │
  Worker A Wakes Up!                                                                           │
  Attempts to:                                                                                 │
  1. Pulse Heartbeat:                                                                          │
     WHERE w_id='w1' AND tok=1 ────────► REJECTED (rowcount = 0)                               │
  2. Commit Checkpoint:                                                                        │
     WHERE task_id='T1' AND tok=1 ──────► REJECTED (rowcount = 0)                               │
  3. Complete Task:                                                                            │
     WHERE task_id='T1' AND tok=1 ──────► REJECTED (rowcount = 0)                               │
        │                                                                                      │
        ▼                                                                                      │
  Throws: StaleExecutionTokenException                                                         │
  - Cleans only its own temp file: `*tok_1_uuid1*.tmp`                                         │
  - Does NOT touch Worker B's files (`*tok_2_*`)                                               │
  - Does NOT touch `media_assets` or `originals/`                                              │
  - Worker A terminates safely and cleanly.                                                    ▼
                                                                                   Worker B completes successfully!
```

### 7.4. الذرية التامة للمعاملات في SQLite:
تُنفذ عمليات سحب المهام، وتحديث الحيازات، وتغيير الحالات دوماً داخل معاملات SQLite فورية موحدة:
```sql
BEGIN IMMEDIATE;
-- التعديل على جدول tasks وجدول active_channel_leases في نفس المعاملة
COMMIT;
```
مما يضمن عدم وجود أي حالة وسيطة يكون فيها السجل موجوداً في جدول وغير موجود في الآخر عبر كافة العمليات والخيوط المتزامنة.

---

## 8. اتساق مخطط الاعتماديات والمهام التابعة (DAG Consistency)

### 8.1. حصر نطاق الاعتماديات داخل القناة الواحدة (Strict Channel-Scoping):
- **القاعدة:** يجب أن تنتمي جميع المهام المترابطة في شجرة الاعتماديات إلى **نفس القناة حصراً (`channel_id`)**.
- **المنع الصارم:** يُحظر حظراً تاماً إنشاء أي اعتمادية بين مهام تعود لقنوات مختلفة (`Cross-Channel Dependencies Prohibited`). محاولة إدراج مهمة تعتمد على مهمة في قناة أخرى تؤدي فوراً إلى رمي `ValidationException` ورفض الطلب لحماية العزل المنطقي (ADR-005).

### 8.2. ثبات شجرة الاعتماديات (Dependency Immutability):
- بمجرد انتقال المهمة من حالة التكوين إلى حالة `QUEUED` أو `RUNNING`، تصبح قائمة الاعتماديات `dependencies_json` **ثابتة وغير قابلة للتعديل نهائياً (Immutable)** لمنع إدخال حلقات تكرارية ديناميكية أثناء التشغيل.

### 8.3. التحقق الذري من الحلقات التكرارية (Atomic Cycle Detection via Kahn's Algorithm):
- لمنع سباق البيانات (Race Conditions) عند تقديم مهام مترابطة في نفس اللحظة عبر خيوط متعددة:
  - يتم فحص الحلقات التكرارية داخل معاملة SQLite فورية `BEGIN IMMEDIATE`.
  - تُبنى شجرة الاعتماديات وتُطبق خوارزمية كان (**Kahn's Topological Sort**).
  - إذا تم اكتشاف أي حلقة ($A \rightarrow B \rightarrow A$)، يتم إلغاء المعاملة فوراً ورمي `ValidationException("Cyclic dependency detected")` دون حفظ أي صف في قاعدة البيانات.

### 8.4. سياسة التعامل مع فشل المهام السابقة (Prerequisite Failure Policy):
1. **إذا فشلت المهمة السابقة (`FAILED`) أو أُلغيت (`CANCELLED`):**
   - يتم تحويل كافة المهام التابعة لها فوراً وبشكل تتالٍ (Cascading Abort) إلى حالة **`CANCELLED`**، مع تسجيل السبب:  
     `"Prerequisite task '{parent_id}' terminated with state {status}"`.
2. **إذا كانت المهمة السابقة في حالة إعادة محاولة مؤقتة (`RETRY_PENDING`):**
   - تظل المهمة التابعة في حالة **`PENDING`** تنتظر النتيجة النهائية للمهمة السابقة دون إلغاء ودون تشغيل مبكر.

---

## 9. المخطط المنقح لقاعدة البيانات (Migration 003 Schema)

يتضمن الترحيل `003_task_queue_schema` الجداول والمؤشرات المحدثة:

```sql
-- Migration 003: Task Queue, Channel Leases, DAG Dependencies and Checkpointing Schema

-- 1. Tasks Table
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    scene_id TEXT REFERENCES scenes(id) ON DELETE SET NULL,
    task_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 10,
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    error_details_json TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    scheduled_at TEXT NOT NULL,
    claimed_at TEXT,
    heartbeat_at TEXT,
    lease_expires_at TEXT,
    execution_token INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    current_step TEXT,
    total_steps INTEGER DEFAULT 1,
    completed_steps INTEGER DEFAULT 0,
    external_job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (channel_id, idempotency_key)
);

-- Optimized Indexes for Dequeue, Fair Scheduling, and Monitoring
CREATE INDEX IF NOT EXISTS idx_tasks_dequeue 
    ON tasks(status, scheduled_at, priority DESC, created_at ASC) 
    WHERE status IN ('queued', 'retry_pending');

CREATE INDEX IF NOT EXISTS idx_tasks_channel_status 
    ON tasks(channel_id, status);

CREATE INDEX IF NOT EXISTS idx_tasks_reaper 
    ON tasks(status, lease_expires_at) 
    WHERE status = 'running';

CREATE INDEX IF NOT EXISTS idx_tasks_pending_deps 
    ON tasks(status, channel_id) 
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_tasks_project 
    ON tasks(project_id);

-- 2. Active Channel Leases Table (Relational Enforcement of max_concurrent_per_channel = 1)
CREATE TABLE IF NOT EXISTS active_channel_leases (
    channel_id TEXT PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    execution_token INTEGER NOT NULL,
    lease_expires_at TEXT NOT NULL
);

-- 3. Task Checkpoints Table (Step-Level Resumption with Token Guard)
CREATE TABLE IF NOT EXISTS task_checkpoints (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_data_json TEXT NOT NULL DEFAULT '{}',
    execution_token INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task 
    ON task_checkpoints(task_id, step_index ASC);
```

---

## 10. التصنيف الصارم لأدلة الاختبارات ومصفوفة القبول المخططة
### (Test Evidence Classification & Planned Acceptance Matrix)

> [!IMPORTANT]
> **إقرار وتصنيف ملزم لأدلة الاختبارات:**
> 1. **الاختبارات المنفذة فعلياً والناجحة بنسبة 100% (49 اختباراً):** تنتمي حصراً إلى المهام السابقة (Task 3.1 إلى Task 3.4)، وتشمل اختبارات بنية المشروع، عزل القنوات، خدمة المشاريع والمشاهد، ونظام التراخيص والخزينة المركزية، وصندوق إرسال التدقيق وسجل التعافي. **لا تمثل هذه الاختبارات إنجازاً برمجياً للمهمة 3.5**.
> 2. **اختبارات المهمة 3.5 (18 سيناريو):** هي **مصفوفة قبول مستهدفة ومخططة (Planned Acceptance Test Matrix)** سيتم بناؤها واختبارها في المرحلة الثانية (التنفيذ البرمجي) للتحقق من مطابقة الكود لهذا التصميم. **لم يتم تشغيل أي منها بعد نظراً لأننا في مرحلة التخطيط والتصميم المعماري فقط**.

### مصفوفة القبول المخططة للمرحلة البرمجية القادمة (18 سيناريو):

| كود الاختبار | سيناريو الاختبار المستهدف | نوع الاختبار | السلوك المتوقع للتحقق البرمجي |
| :--- | :--- | :--- | :--- |
| **TC-3.5-01** | منع تكرار الإدراج (Idempotency Insert) | Unit / Repo | عدم تكرار الصف وإرجاع كائن المهمة القائم |
| **TC-3.5-02** | التنافس المتزامن على السحب الذري (Atomic Claim) | Concurrency | حصول كل عامل على مهمة مستقلة بدون تكرار (`rowcount=1`) |
| **TC-3.5-03** | انهيار التطبيق بعد إنشاء الوظيفة الخارجية وقبل حفظ المعرف | Crash Recovery | تفعيل بروتوكول النتيجة المجهولة وتعليق الإرسال التلقائي |
| **TC-3.5-04** | انتهاء عقد الحيازة واكتشاف العامل المتجمد | Recovery | استعادة المهمة عبر المسترد، زيادة `execution_token`، وإعادة جدولتها |
| **TC-3.5-05** | صد العامل المتجمد برمز الحيازة وحذف ملفاته المؤقتة الخاصة | Fencing Token | إلقاء `StaleExecutionTokenException` وحذف ملفات الرمز فقط دون لمس ملفات العامل الجديد |
| **TC-3.5-06** | انهيار النظام ومسار العزل الآمن عند اختلاف SHA-256 | Journal Recovery | حظر حذف الملف من `originals/`، ونقله إلى `quarantine/` في حال عدم التطابق واعتماد السليم بعد المطابقة |
| **TC-3.5-07** | التزامن بين القنوات والتسلسل الأحادي عبر `active_channel_leases` | Governance | رفض تشغيل مهمتين لنفس القناة متزامناً عبر قيد الجدول وتمرير القنوات المختلفة بالتوازي |
| **TC-3.5-08** | صمود النظام وتفعيل مهلة الانتظار وإعادة المحاولة عند تنافس `SQLITE_BUSY` | DB Lock / Stress | معالجة تنافس الأقفال على مستوى الاتصال دون زيادة عداد محاولات المهمة |
| **TC-3.5-09** | الاستئناف المرحلي وتخطي الخطوات السابقة المنجزة | Integration | قراءة نقاط الحفظ والبدء مباشرة من الخطوة غير المكتملة دون تكرار الخطوات السابقة |
| **TC-3.5-10** | دعم `Retry-After` بصيغتي الثواني وتاريخ HTTP وفحص TTL الشامل | Lifecycle | معالجة انحراف الساعة وتطبيق فحص الصلاحية القصوى وتحويل المتجاوز إلى `FAILED` |
| **TC-3.5-11** | الفشل الفوري لانتهاكات التراخيص وقفل التدقيق (ADR-008) | Compliance | تحويل المهمة فوراً إلى `FAILED` دون أي محاولة إعادة عند انتهاك القواعد القانونية |
| **TC-3.5-12** | فك حجب الاعتماديات المتسلسلة (DAG Execution) | DAG / Logic | انتقال المهمة التابعة تلقائياً من `PENDING` إلى `QUEUED` فور اكتمال مهامها السابقة |
| **TC-3.5-13** | اكتشاف الحلقات التكرارية ورفضها بخوارزمية كان عند الإنشاء | DAG / Validation | رفض المخطط الذي يحوي حلقات ($A \rightarrow B \rightarrow A$) فوراً بإلقاء `ValidationException` |
| **TC-3.5-14** | الفشل التتالي وإلغاء المهام التابعة تلقائياً | DAG / Cascade | تحويل المهام التابعة تلقائياً إلى `CANCELLED` عند فشل المهمة المعتمد عليها |
| **TC-3.5-15** | الإلغاء التعاوني الصريح أثناء تنفيذ المهمة | Control | توقف العامل بأمان، تنظيف ملفات الكاش، وتحويل الحالة لـ `CANCELLED` |
| **TC-3.5-16** | تكرار الانهيار حتى استنفاد الحد الأقصى للمحاولات | Resilience | زيادة عداد المحاولات وصولاً إلى `FAILED` عند استنفاد المحاولات المسموحة |
| **TC-3.5-17** | التعافي الشامل عند إقلاع النظام البارد (Cold Boot Resumption) | Cold Boot | فحص كافة المهام العالقة وتنظيف الحيازات المنتهية عند بدء تشغيل التطبيق |
| **TC-3.5-18** | التحقق من انعدام المعاملات الطويلة أثناء استدعاءات الشبكة والرندر | Performance / Benchmark | التأكد من أن أزمنة معاملات SQLite تبقى في نطاق الميكروثانية ولا تحبس القاعدة |

---

## 11. تحليل التأثير البرمجي والملفات المستهدفة في مرحلة التنفيذ (File Impact Analysis)

عند صدور الموافقة الصريحة للانتقال إلى مرحلة التنفيذ البرمجي، سيقتصر العمل على:

### أ) ملفات الثوابت والمخطط والنماذج (Schema & Models):
1. [`src/core/constants.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/core/constants.py): إضافة تعداد `TaskStatus` و `TaskType` و `TaskPriority`.
2. [`src/database/migrations.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/database/migrations.py): إضافة الترحيل `003_task_queue_schema` لجداول `tasks` و `active_channel_leases` و `task_checkpoints`.
3. [`src/database/models.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/database/models.py): نماذج Pydantic للتحقق الصارم (`TaskModel`, `TaskCreate`, `TaskUpdate`, `TaskCheckpointModel`, `ActiveChannelLeaseModel`).

### ب) طبقة المستودعات والخدمات (Repositories & Services):
4. `src/database/repositories/task_repository.py`: مستودع إدارة المهام، السحب الذري المشروط، حيازة `active_channel_leases`، فحص نبض الحياة، وإدارة نقاط الحفظ.
5. `src/services/task_service.py`: واجهة خدمة المهام الأساسية (إنشاء مهام، فحص الاعتماديات واكتشاف الحلقات التكرارية بخوارزمية كان، الاستعلام، الإلغاء، وإدارة التراجع الأسي).
6. `src/core/queue/worker_pool.py`: إدارة حوض العمال (Worker Pool)، ضبط حدود التزامن وعزل القنوات، وتنفيذ الحلقات الخلفية.
7. `src/core/queue/task_reaper.py`: محرك المسترد المسؤول عن تنظيف المهام العالقة وفحص انتهاء الحيازة واستئناف التشغيل عند الإقلاع.
8. [`src/services/__init__.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/services/__init__.py): تصدير `TaskService`.

### ج) ملفات الاختبارات (Test Suites):
9. `tests/test_task_queue_and_resumption.py`: جناح اختبارات شامل ينفذ ويتحقق برمجياً من الحالات الـ 18 المحددة في مصفوفة الاختبارات المخططة.

---

## 12. إقرار الالتزام وبوابة الموافقة (Approval Gate)

> [!IMPORTANT]
> **التزام صارم بالضوابط الهندسية للمرحلة الأولى:**
> - تم تحديث وتنقيح وثيقة التصميم المعماري فقط دون إجراء أي تعديل على الكود البرمجي للمشروع.
> - لم يتم تشغيل أي Migration ولم يتم التعديل على قاعدة البيانات أو إنشاء أي ملفات تشغيلية.
> - العمل متوقف تماماً عند بوابة الاعتماد الحالية، بانتظار مراجعتكم وموافقتكم الصريحة للانتقال إلى المرحلة الثانية (التنفيذ البرمجي للمهمة 3.5).
