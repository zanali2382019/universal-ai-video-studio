# خارطة طريق ومحطات المرحلة الثانية (Phase 2 Roadmap & Milestones)
## استوديو الإنتاج المرئي بالذكاء الاصطناعي | Universal AI Video Production Studio
**تاريخ الوثيقة:** 16 نوفمبر 2026  
**الحالة:** وثيقة تخطيط تنفيذي محدثة للمرحلة الثانية (قيد المراجعة — بانتظار الاعتماد الصريح)  

---

## 1. أهداف ومخرجات المرحلة الثانية (Phase 2 Objectives)

تختص المرحلة الثانية بالوصول إلى **المخطط المعماري الشامل وتصميم المنتج (Product Blueprint & System Architecture)**، مع توثيق كافة القرارات الهندسية والمفاضلات والبدائل المتاحة، والتحقق من اكتمال رحلة المستخدم في النسخة الأولية، تمهيداً لعرضها على المستخدم للاعتماد الصريح قبل كتابة أي كود برمجي.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│             المرحلة 2: التصميم المعماري وهندسة المنتج (Architecture & Specs)            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  M2.1: المخطط العام والمتطلبات الوظيفية وغير الوظيفية (Blueprint & Requirements)       │
│  M2.2: معمارية القنوات المتعددة والعزل المنطقي (Multi-Channel Logical Architecture)     │
│  M2.3: مواصفات محرك القوالب العالمي والقوالب الأساسية (Template Engine Specs)          │
│  M2.4: هندسة خط الإنتاج متعدد المسارات والأصول والتراخيص (AI Production, MAM & License)│
│  M2.5: معمارية الرندر بالأبعاد المتعددة والجودة والامتثال (Rendering & Compliance)     │
│  M2.6: استراتيجية التكامل، حدود SQLite، والواجهة ونطاق MVP (Integrations & MVP Scope)  │
│  M2.7: سجل القرارات المعمارية الشامل (Architecture Decision Records - ADRs)            │
├────────────────────────────────────────────────────────────────────────────────────────┤
│     بوابة الاعتماد النهائي للمرحلة 2 (Awaiting Explicit User Approval Gate)            │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. جدول حزم العمل والمستندات الهندسية المرتبطة

| الحزمة (Package) | موضوع حزمة العمل | المستند الهندسي المرتبط | حالة التوثيق |
| :--- | :--- | :--- | :--- |
| **WP 2.1** | المخطط العام للمنصة وتكامل الواجهة الرسومية مع النواة | [Product_Blueprint.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Product_Blueprint.md) | 🟢 محدث ومكتمل |
| **WP 2.2** | المتطلبات الوظيفية الشاملة ومكونات Minimal GUI | [Functional_Requirements.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Functional_Requirements.md) | 🟢 محدث ومكتمل |
| **WP 2.3** | المتطلبات غير الوظيفية ومحددات SQLite ومعايير الصوت | [Non_Functional_Requirements.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Non_Functional_Requirements.md) | 🟢 محدث ومكتمل |
| **WP 2.4** | معمارية العزل المنطقي المنظم للقنوات المتعددة | [Multi_Channel_Architecture.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Multi_Channel_Architecture.md) | 🟢 محدث ومكتمل |
| **WP 2.5** | محرك القوالب العالمي والقوالب الأساسية للكرتون والوثائقي | [Template_Engine_Specification.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Template_Engine_Specification.md) | 🟢 محدث ومكتمل |
| **WP 2.6** | محرك الإنتاج متعدد المسارات (T2V, I2V, V2V, Ingest) | [AI_Production_Engine.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/AI_Production_Engine.md) | 🟢 محدث ومكتمل |
| **WP 2.7** | إدارة الأصول، تتبع التراخيص، ونقد نماذج الوجوه للكرتون | [Media_Asset_Management.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Media_Asset_Management.md) | 🟢 محدث ومكتمل |
| **WP 2.8** | رندر الأبعاد الثلاثة (9:16, 1:1, 16:9) ومواصفات الصوت المرنة | [Video_Rendering_Architecture.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Video_Rendering_Architecture.md) | 🟢 محدث ومكتمل |
| **WP 2.9** | محرك الميتا-داتا النوعي وسياسات YouTube و COPPA الرسمية | [Quality_Compliance_Specification.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Quality_Compliance_Specification.md) | 🟢 محدث ومكتمل |
| **WP 2.10** | تجريد المزودين وحدود SQLite ومؤشرات الترقية لطابور موزع | [Integration_Strategy.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Integration_Strategy.md) | 🟢 محدث ومكتمل |
| **WP 2.11** | ترسيم نطاق MVP برحلة مستخدم متكاملة عبر الواجهة والنواة | [MVP_Scope.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/MVP_Scope.md) | 🟢 محدث ومكتمل |
| **WP 2.12** | سجل القرارات المعمارية الشامل والبدائل والمفاضلات | [Architecture_Decision_Records.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Architecture_Decision_Records.md) | 🟢 مكتمل وجديد |

---

## 3. القرارات المعمارية الجوهرية المعروضة للاعتماد (Decisions Awaiting Sign-off)

1. **القرار المعماري 1:** اعتماد بنية مزدوجة في MVP: **Headless CLI Core** كنواة معالجة داخلية، مع **Minimal Local GUI** كواجهة مرئية محلية سهلة الاستخدام تغطي رحلة المستخدم كاملة.
2. **القرار المعماري 2:** دعم **تعددية مسارات التوليد** (T2V, I2V, واستيراد الوسائط الجاهزة) وعدم حصر المنصة في مسار واحد.
3. **القرار المعماري 3:** جعل **مواصفات الصوت والجهارة قابلة للتهيئة بالكامل** لكل قناة وقالب، مع إلزام مخرجات الصوت بمانع التشويه الحاسم (True Peak Limiter < -1.0 dBFS).
4. **القرار المعماري 4:** اعتماد محرك **ميتا-داتا نوعي يربط جودة العنوان والغلاف والوصف بالبيانات الفعلية** وتجنب فرض أوزان مئوية نظرية غير مثبتة.
5. **القرار المعماري 5:** تصنيف عزل القنوات كـ **عزل منطقي منظم ومحكم برمجياً** داخل مجلدات وقواعد بيانات الاستوديو.
6. **القرار المعماري 6:** عدم اعتماد أي نموذج أو نسبة اتساق للوجوه الكرتونية (مثل ArcFace) قبل إجراء **معايرة واختبارات تجريبية عملية** في المرحلة الرابعة.
7. **القرار المعماري 7:** تشغيل طابور المهام في MVP عبر **SQLite Persistent Queue** (بوضع WAL ومهلة 10 ثوانٍ ومفاتيح Idempotency لمنع التكرار) مع تحديد معايير واضحة للانتقال لطابور موزع (Redis/Celery) عند بلوغ أكثر من 10 عمال متزامنين أو توزيع الخوادم.
8. **القرار المعماري 8:** بناء **نظام لتتبع وتوثيق تراخيص الوسائط** (`licenses.json`) لضمان حقوق الاستخدام التجاري والإسناد القانوني قبل التصدير.
9. **القرار المعماري 9:** دعم الأبعاد الثلاثة المعتمدة (**9:16 و 1:1 و 16:9**) وإدارة إفصاح المحتوى الاصطناعي وسياسة COPPA وفق القواعد الرسمية لـ YouTube.

---

## 4. إقرار الحظر التشغيلي وبوابة الموافقة (Approval Gate Status)

> [!CAUTION]
> **حالة الاعتماد الحالية: بانتظار موافقة المستخدم الصريحة (Pending User Sign-off).**  
> لم يتم تعديل حالة المرحلة إلى "معتمدة"، ولا يجوز البدء في كتابة كود برمجي أو واجهات أو تثبيت مكتبات أو إنتاج فيديوهات قبل الحصول على موافقة كتابية صريحة من المستخدم على القرارات المسجلة في وثيقة [Architecture_Decision_Records.md](file:///E:/AI-Projects/universal-ai-video-studio/docs/Architecture_Decision_Records.md).
