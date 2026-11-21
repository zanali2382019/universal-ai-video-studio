# تقرير إنجاز المرحلة الثانية: المعلم الثالث — المهمة 3.5
## Task 3.5 — Stage 2: Milestone 3 Completion Report
### Worker Pool, Heartbeat Daemon & TaskReaper

**تاريخ الإنجاز:** 16 نوفمبر 2026  
**المرحلة:** المرحلة الثالثة (Phase 3: Core Engine Development & Integration)  
**الحالة:** 🟢 **مكتمل بنجاح 100% ومختبر بالكامل (15 اختباراً جديداً + 75 اختباراً سابقاً = 90 اختباراً ناجحاً بنسبة 100%)**  
**بوابة العبور (Milestone Gate):** 🛑 **تم التوقف التام عند حدود المعلم الثالث، ولا يتم البدء بالمعلم الرابع (Checkpoint & Crash Recovery Integration) إلا بعد المراجعة والموافقة الصريحة.**

---

## 1. ملخص تنفيذي (Executive Summary)

وفقاً للتفويض الصادر للمعلم **Task 3.5 — Stage 2: Milestone 3**، تم بناء طبقة تشغيل وتنفيذ المهام الخلفية بالكامل:
1. **`WorkerPool` و`WorkerThread` و`WorkerProcess`:** استهلاك المهام بذرية باستخدام `atomic_claim_next_task`، مع احترام عزل القنوات (`max_concurrent_per_channel = 1`)، وسقف العمال المعتمد `min(CPU cores, 4)`، ودعم التوازي عبر الخيوط وعبر العمليات المستقلة (Multiprocessing).
2. **`HeartbeatDaemon`:** نبضات قلب دورية كل 15–20 ثانية لتجديد حيازة القناة، مع الرصد الفوري لفقدان الحيازة أو إلغاء التوكن ووقف العامل عن الكتابة.
3. **`TaskReaper`:** خدمة استرداد المهام منتهية الحيازة بأمان ذري محمي بالـ Fencing Token، وتطبيق التراجع الأسي (Exponential Backoff مع Jitter)، وفحص الصلاحية القصوى الشامل (TaskTTL = 24h)، والتعامل مع ترويسة `Retry-After` والحالات غير المؤكدة `UNKNOWN_EXTERNAL_STATE`.
4. **`Graceful Shutdown`:** آلية الإيقاف الآمن والرشيق التي تمنع سحب مهام جديدة وتمنح المهام النشطة مهلة للإنجاز وتحرير الحيازة.
5. **معالجة الملاحظتين السابقتين:** تعزيز حماية المخططات الموجهة (DAG Validation) لتتم داخل معاملة `BEGIN IMMEDIATE`، وإلزام العمال بتمرير `execution_token` في كافة التحولات التشغيلية للمهام الجارية (`RUNNING`).

---

## 2. نتائج الفحص القبلي للكود (Source Inspection Summary)

قبل التعديل، تم فحص المكونات الفعلية:
- **`TaskRepository`:** يوفر `atomic_claim_next_task` و`renew_lease` و`release_lease` و`reap_stale_task`.
- **`TaskService`:** يوفر آلة الحالات الصارمة `transition_status` والتحقق من التبعيات.
- **`AuditService` و`FileOperationRepository`:** يعتمدان الأقفال الفيزيائية للقناة وجدول `file_operations` كـ Single Source of Truth لحركات الملفات.
- **`DatabaseManager`:** يوفر معاملات `BEGIN IMMEDIATE` و`busy_timeout=15,000ms` مع `PRAGMA foreign_keys = ON` و`WAL mode`.

### معالجة الملاحظتين الهندسيتين السابقتين:
1. **الملاحظة الأولى (Atomic DAG Validation):**
   - في [`src/database/repositories/task_repository.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/database/repositories/task_repository.py#L33-L115)، أُضيف وسيط اختياري `pre_insert_callback(conn)` يُنفذ داخل معاملة `BEGIN IMMEDIATE` الفورية.
   - في [`src/services/task_service.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/services/task_service.py#L225-L235)، تم ربط فحص `_validate_dag` ليقرأ المهام الحالية داخل المعاملة المقفولة عبر `conn` قبل الإدراج مباشرة، مما أزال تماماً نافذة السباق الزمني عند إضافة تبعيات متزامنة.
2. **الملاحظة الثانية (Strict Operational Fencing Token):**
   - في [`src/services/task_service.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/services/task_service.py#L320-L335)، تم فرض شرط صارم يمنع أي عامل من تعديل حالة مهمة `RUNNING` إلى (`COMPLETED`, `FAILED`, `RETRY_PENDING`, `RETRY_PENDING_APPROVAL`) دون تمرير `execution_token` الخاص به، مما يمنع تجاوز الـ Fencing Token.

---

## 3. المكونات والملفات المنفذة (Implemented Components)

### 3.1 خادم نبضات القلب (Heartbeat Daemon)
**[`src/services/heartbeat_daemon.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/services/heartbeat_daemon.py):**
- يعمل في خيط مستقل مخصص للمهمة النشطة.
- يرسل نبضة تجديد عبر `task_repo.renew_lease` كل 15 ثانية لتمديد الإيجار (افتراضياً 60 ثانية).
- يشترط مطابقة المفاتيح الأربعة: `(channel_id, task_id, worker_id, execution_token)`.
- إذا أعاد الاستعلام `0 rows` أو رُمي `StaleExecutionTokenException`:
  - يُعيّن فوراً حدث `is_lease_lost`.
  - يستدعي الدالة التراجعية `on_lease_lost()`.
  - يُشعر العامل التشغيلي بالتوقف الفوري وإسقاط النتائج لمنع الكتابة غير المصرح بها.

---

### 3.2 خدمة استرداد المهام (TaskReaper)
**[`src/services/task_reaper.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/services/task_reaper.py):**
- تفحص المهام التي بحالة `RUNNING` والتي انتهت حيازتها (`lease_expires_at <= now`).
- تطبق معادلة التراجع الأسي مع التشويش العشوائي (Jitter +/- 20%):
  $$\text{Backoff} = \min(300\text{s},\, 5 \times 2^{\text{retry\_count}+1}) \times \text{uniform}(0.8,\, 1.2)$$
- تفحص الصلاحية القصوى الشاملة للمهمة (TaskTTL = 24 ساعة):
  - إذا كان $\text{now} + \text{Backoff} > \text{created\_at} + 24\text{h}$، أو استُنفدت المحاولات (`retry_count + 1 >= max_retries`):
    تتحول المهمة فوراً إلى **`FAILED`** وتُلغى كافة المهام التابعة لها عبر `TaskService`.
  - إذا كانت ضمن المهلة: تتحول إلى **`RETRY_PENDING`** مع جدولة `scheduled_at`.
- تستدعي `task_repo.reap_stale_task` الذي يتحقق من مطابقة `stale_worker_id` و`stale_token` ويزيد التوكن بمقدار 1، مما يضمن استحالة مساس الـ Reaper بحيازة عامل أحدث استلم المهمة بالفعل.
- تنظف الإيجارات المنتهية اليتيمة في `active_channel_leases`.

---

### 3.3 حوض العمال وسياسة الإعادة والإيقاف الرشيق (Worker Pool)
**[`src/services/worker_pool.py`](file:///E:/AI-Projects/universal-ai-video-studio/src/services/worker_pool.py):**

1. **`RetryPolicy`:**
   - تحليل ترويسة `Retry-After` المعيارية وفق RFC 7231 / RFC 9110 (بالثواني Delta-seconds أو التاريخ الرسمي HTTP-date).
   - معالجة انحراف الساعة (Clock Skew): إذا كان التاريخ متأخراً أو منتهياً، يتم تطبيق مهلة أمان عشوائية تشويشية $\text{uniform}(3,\, 7)\text{s}$.
   - تصنيف الحالات المجهولة (`is_unknown_external_state`): تحويل المهمة فوراً إلى **`RETRY_PENDING_APPROVAL`** لمنع إعادة المحاولة التلقائية والتكلفة المالية المزدوجة.
   - التأخيرات الطويلة ($> 1800\text{s}$): التحويل إلى `RETRY_PENDING_APPROVAL` لإشعار المشغل.
2. **`WorkerThread`:**
   - خيط عامل لسحب المهام عبر `atomic_claim_next_task`، وتشغيل `HeartbeatDaemon`، واستدعاء معالج التنفيذ.
   - فحص `heartbeat.is_lease_lost`: عند فقدان الإيجار، يُسقط المخرجات فوراً ولا يُحدث قاعدة البيانات.
   - عند النجاح: ينقل المهمة إلى `COMPLETED` بتوكن التنفيذ.
   - عند الفشل المؤقت: يطبق `RetryPolicy` وينقل إلى `RETRY_PENDING` أو `FAILED` بتوكن التنفيذ.
3. **`WorkerProcess` (`_process_worker_entrypoint`):**
   - عملية نظام تشغيل مستقلة كلياً (`multiprocessing.Process`) تفتح اتصالات SQLite مستقلة بمدير معاملات خاص، وتنفذ المطالبة الذرية والنبضات والتنفيذ الفعلي عبر حدود العمليات.
4. **`WorkerPool`:**
   - إدارة حوض العمال بالعدد المحدد (افتراضياً 2، وبسقف `min(CPU cores, 4)`).
   - دعم بيئتي التشغيل: `backend="thread"` و `backend="process"`.
   - **`shutdown(timeout_sec)` (الإيقاف الآمن):**
     - تفعيل راية الإيقاف لمنع سحب أي مهام جديدة.
     - منح المهام النشطة مهلة زمنية لإكمال المعالجة وتحرير الحيازة.
     - إنهاء العمليات غير المستجيبة بأمان بعد انقضاء المهلة.

---

## 4. أدلة ونتائج الاختبارات الشاملة (90 اختباراً)

تم إنشاء ملف اختبارات شامل مخصص للمعلم الثالث:
**[`tests/test_worker_pool.py`](file:///E:/AI-Projects/universal-ai-video-studio/tests/test_worker_pool.py)**

يغطي 15 سيناريو اختبارياً دقيقاً، مع الاعتماد الحصري على قواعد بيانات SQLite حقيقية في مجلدات مؤقتة:

| رقم السيناريو | دالة الاختبار | الهدف وسلوك التحقق | النتيجة |
| :---: | :--- | :--- | :---: |
| 1 | `test_worker_claim_and_execution` | تشغيل Worker، سحب مهمة من الطابور، تنفيذها، ونقلها إلى `COMPLETED` مع تحرير الحيازة. | ✅ نجح (OK) |
| 2 | `test_intra_channel_single_worker_lock` | منع عاملين متنافسين من امتلاك نفس القناة بالتزامن؛ حيازة واحدة فقط لكل قناة (`max_concurrent_per_channel = 1`). | ✅ نجح (OK) |
| 3 | `test_inter_channel_parallelism_under_worker_limit` | تشغيل مهام قنوات مختلفة بالتوازي الكامل وفق سقف العمال المتاح. | ✅ نجح (OK) |
| 4 | `test_heartbeat_lease_renewal` | خادم نبضات القلب يجدد حيازة القناة ويمدد `lease_expires_at` بنجاح دورياً. | ✅ نجح (OK) |
| 5 | `test_heartbeat_rejected_with_stale_token` | رفض نبضات القلب عند اختلاف التوكن ورصد `is_lease_lost` فوراً. | ✅ نجح (OK) |
| 6 | `test_lease_expiry_and_reaper_recovery` | قيام `TaskReaper` برصد الإيجار المنتهي، وزيادة التوكن، وإعادة جدولة المهمة بتراجع أسي. | ✅ نجح (OK) |
| 7 | `test_reaper_cannot_delete_newer_lease` | منع الـ Reaper المتأخر من حذف أو مساس حيازة عامل أحدث يحمل توكناً أعلى. | ✅ نجح (OK) |
| 8 | `test_stale_worker_prevented_from_completing` | منع العامل القديم من إكمال المهمة بعد استردادها ورمي `StaleExecutionTokenException`. | ✅ نجح (OK) |
| 9 | `test_retry_and_backoff_evaluation` | فحص حسابات التراجع الأسي وتشويش Jitter وحالات `UNKNOWN_EXTERNAL_STATE`. | ✅ نجح (OK) |
| 10 | `test_retry_after_and_ttl_enforcement` | احترام ترويسة `Retry-After` (ثوانٍ وتاريخ) وتحويل المهمة إلى `FAILED` عند تجاوز مهلة 24 ساعة. | ✅ نجح (OK) |
| 11 | `test_graceful_shutdown` | الإيقاف الرشيق للعمال والسماح للمهمة الجارية بالاكتمال الآمن وتحرير الحيازة. | ✅ نجح (OK) |
| 12 | `test_no_new_claims_during_shutdown` | منع العمال تماماً من سحب مهام جديدة بمجرد إطلاق أمر الإيقاف. | ✅ نجح (OK) |
| 13 | `test_multiprocessing_concurrency` | **اختبار التزامن عبر عمليات نظام تشغيل مستقلة (`multiprocessing.Process`) تشترك في ملف SQLite حقيقي.** | ✅ نجح (OK) |
| 14 | `test_multiple_sqlite_connections_on_real_temp_db` | اختبار تنافس عدة اتصالات SQLite متزامنة على ملف حقيقي وسحب 6 مهام فريدة دون تصادم. | ✅ نجح (OK) |
| 15 | `test_sqlite_busy_handling_in_worker_pool` | صمود الحوض تحت ضغط وتنافس 4 عمال متزامنين دون أي خطأ `SQLITE_BUSY` غير معالج. | ✅ نجح (OK) |

### نتائج الحزمة الاختبارية الكاملة للمشروع (Full Regression Suite):
```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```
```
Ran 90 tests in 22.165s

OK
```
- **اختبارات المهام 3.1 إلى 3.4 (السابقة):** 49 اختباراً — **ناجحة 100%**.
- **اختبارات Task 3.5 — M1 (مستودع المهام):** 12 اختباراً — **ناجحة 100%**.
- **اختبارات Task 3.5 — M2 (خدمة المهام والاعتماديات):** 14 اختباراً — **ناجحة 100%**.
- **اختبارات Task 3.5 — M3 (حوض العمال والمسترد والنبضات):** 15 اختباراً — **ناجحة 100%**.
- **الإجمالي العام:** 90 اختباراً ناجحاً بنسبة 100% دون أي أخطاء أو تراجعات (Zero Regressions).

---

## 5. الالتزام بحدود التفويض وبوابة العبور (Milestone Gate Compliance)

1. **الالتزام بالنطاق المصرح به:**
   - تم تنفيذ حوض العمال ونبضات القلب والـ TaskReaper والإيقاف الرشيق حصراً.
   - تم حل الملاحظتين الهندسيتين السابقتين بنجاح.
2. **الممنوعات التي تم الالتزام بها بدقة:**
   - لم يتم استدعاء أي خدمات ذكاء اصطناعي حقيقية أو إنفاق مالي.
   - لم يتم تنفيذ أي رندر أو تكامل مع FFmpeg (Task 3.6).
   - لم يتم البدء بدمج نقاط الاستئناف مع استعادة ملفات الأصول الفيزيائية (Milestone 4).
   - لم يُستخدم Redis أو Celery.
   - لم يتم إجراء أي تعديل على قاعدة بيانات الإنتاج.

---

## 6. الخطوة القادمة المقترحة (Next Milestone Scope)

المعلم التالي المجدول معمارياً:
**Task 3.5 — Stage 2: Milestone 4: Checkpoint & Crash Recovery Integration**
ويشمل:
1. دمج نقاط الاستئناف المرحلية `task_checkpoints` مع خطوات تنفيذ خط الإنتاج.
2. بروتوكول مصالحة الملفات الفيزيائية على القرص (`reconcile_recovery`) استناداً لجدول `file_operations` وخزينة `media_vault`.
3. التحقق من تطابق بصمات SHA-256 قبل وبعد الانهيار، واعتماد مسار العزل الآمن (`quarantine/`) لمنع تدمير الأصول السليمة.
4. استئناف المهام من آخر خطوة ناجحة دون إعادة التوليد المكلف عند الصفر المالي ($0 Resumption).

**تنبيه:** يتوقف النظام الآن بشكل كامل عند بوابة العبور بانتظار مراجعة المستخدم وإجراء تدقيق التحقق النهائي واعتماد الانتقال إلى Milestone 4.

