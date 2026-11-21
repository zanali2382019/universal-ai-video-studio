# إدارة الأصول والوسائط والشخصيات والتراخيص (Asset, MAM & License Management)
## استوديو الإنتاج المرئي بالذكاء الاصطناعي | Universal AI Video Production Studio
**تاريخ الوثيقة:** 16 نوفمبر 2026  
**الحالة:** مواصفة هندسية مدققة ومحدثة للمرحلة الثانية  

---

## 1. البنية الشاملة لنظام إدارة الأصول (Universal MAM Architecture)

يقوم نظام إدارة الأصول (MAM) على إدارة ثلاثة محاور متكاملة:
1. **مستودع الوسائط العامة (General Media Vault):** لحفظ وتنظيم الصور، الكليبات، الموسيقى، والخطوط.
2. **نظام تتبع التراخيص وسجل التدقيق القانوني (Asset License & Audit Log System):** لتوثيق السند القانوني ومصادر وتراخيص كل أصل رقمي مستخدم.
3. **وحدة الشخصيات التخصصية الاختيارية (Optional Character Anchor Unit):** وحدة ملحقة اختيارية تُفعل فقط لمشاريع الرسوم المتحركة والسلاسل القصصية.

```mermaid
graph TD
    MAM[Universal Asset & License Management System]

    subgraph General Media Vault [إلزامي لجميع المشروعات]
        BrandingVault[أصول الهوية: شعارات، شاشات، خطوط]
        StockVault[كليبات B-Roll وصور جاهزة]
        AudioVault[مكتبة الصوت: SFX وموسيقى تصويرية]
    end

    subgraph License & Legal Registry [نظام إدارة التراخيص والتدقيق]
        LicenseDB[سجل تراخيص الأصول: licenses.json]
        AuditLog[سجل التدقيق غير القابل للتعديل: license_audit.log]
        ImportAllow[السماح بالاستيراد والمعاينة والتجربة الداخلية]
        ExportGate[بوابة التصدير الصارمة: حظر التصدير عند بقاء الحقوق غير محسومة]
    end

    subgraph Optional Character Anchor Unit [وحدة اختيارية تخصصية]
        ModelSheet[ورقة زوايا الشخصية Turnaround]
        StyleRefs[المراجع البصرية والأوزان]
        ConsistencyBenchmarker[بروتوكول المعايرة البصرية التجريبي]
    end

    MAM --> General Media Vault
    MAM --> License & Legal Registry
    MAM -.->|تُفعل عند الحاجة فقط| Optional Character Anchor Unit
```

---

## 2. نظام إدارة التراخيص والأساس القانوني وبوابة التصدير الصارمة (Strict License & Legal Basis Gate)

> [!IMPORTANT]
> **قواعد إدارة التراخيص وحظر التصدير:**  
> 1. **الاستيراد والمعاينة الداخلية:** يُسمح باستيراد ومعاينة وتجربة الوسائط مجهولة الترخيص (`unverified` / `pending_review`) داخل فضاء المشروع والمحرر الداخلي لتمكين صانع المحتوى من التجربة والمونتاج.
> 2. **حظر التجاوز الشكلي:** لا يجوز إطلاقاً تجاوز حالة الترخيص المجهول أو غير المحسوم بمجرد نقرة أو إقرار سطحي من المستخدم بأنه "يتحمل المسؤولية".
> 3. **إلزامية السند القانوني:** لا يُعتمد أي أصل للتصدير إلا بتسجيل **أساس قانوني صريح وموثق (Documented Legal Basis)** يندرج تحت أحد التصنيفات المعتمدة:
>    * **الملكية الأصلية (Proprietary Ownership):** وسائط منشأة أو مسجلة أصلياً بواسطة القناة.
>    * **الترخيص التجاري (Commercial License):** ترخيص تجاري مدفوع أو سارٍ مع إرفاق رقم الفاتورة أو رمز المعاملة.
>    * **الإذن الكتابي المباشر (Direct Written Permission):** تفويض رسمي موثق من صاحب الحقوق.
>    * **الملك العام المثبت (Verified Public Domain / CC0):** مع توثيق الرابط والمصدر المثبت لحالة الملك العام.
> 4. **حظر التصدير الخارجي:** يُحظر التصدير النهائي الخارجي لأي فيديو طالما ظلت حقوق أحد أصوله غير محسومة قانونياً.
> 5. **تسجيل الاستثناءات في سجل التدقيق (Audit Log):** في حال اعتماد استثناء رسمي، يجب تسجيله في سجل تدقيق غير قابل للتلاعب (`license_audit.log`) يتضمن: السبب المفصل، السند المعتمد، المصدر، بيانات المعتمد، والتوقيت الزمني.
> 6. **إخلاء الضمان القانوني:** تسجيل الاستثناء يمثل توثيقاً إدارياً داخلياً للمسؤولية والمصدر، **ولا يمثل بأي حال من الأحوال ضماناً أو صكاً قانونياً لصحة الترخيص**.

### هيكل سجل التراخيص والتدقيق المحدث (`licenses.json`):
```json
{
  "asset_id": "aud_sfx_pop_01",
  "file_path": "media_vault/sfx/cartoon_pop.wav",
  "asset_type": "audio_sfx",
  "provenance": {
    "source_name": "Freesound / Internal Library",
    "source_url": "https://freesound.org/people/sample/sounds/12345/",
    "author_creator": "SoundDesignerX",
    "legal_basis": "verified_public_domain_cc0",
    "license_evidence": "URL_verified_CC0_declaration",
    "commercial_use_permitted": true,
    "attribution_required": false,
    "attribution_text": null,
    "verification_status": "verified",
    "audit_record": {
      "approved_by": "user_admin",
      "timestamp": "2026-11-16T11:00:00Z",
      "legal_basis_justification": "Public domain sound confirmed via author CC0 upload waiver",
      "is_legal_warranty": false
    }
  }
}
```

---

## 3. مراجعة صلاحية نماذج قياس اتساق الشخصيات الكرتونية (Cartoon Consistency Review)

> [!CAUTION]
> **مراجعة نقدية لنماذج ArcFace و InsightFace في الرسوم الكرتونية:**  
> تم تدريب نماذج ArcFace و InsightFace على ملايين الصور الفوتوغرافية للوجوه البشرية الحقيقية (Real Human Faces).  
> هذه النماذج **غير صالحة أو ضعيفة جداً** عند تطبيقها على شخصيات الرسوم المتحركة ثلاثية الأبعاد أو الكرتون 2D أو الكائنات الخيالية (مثل شخصية تومي ذات العيون الكبيرة المبالغ فيها أو شريحة بيتزا بعينين)، حيث تفشل خوارزميات كشف المعالم (Facial Landmark Detection) في التعرف على الوجه الكرتوني من الأساس.

### البدائل الهندسية المعيارية المقترحة للاختبار:
1. **نماذج الرؤية ذاتية الإشراف العامة (Self-Supervised Vision Models):**
   * استخدام نموذج **DINOv2** أو **CLIP Image Embeddings**، حيث تستخرج هذه النماذج متجهات السمات البصرية العامة (Texture, Shape, Color, Geometry) وتعمل بكفاءة عالية على الصور الفنية والكرتونية بنفس كفاءة الصور الحقيقية.
2. **بروتوكول المعايرة التجريبية (Empirical Calibration Protocol):**
   * **القاعدة الصارمة:** يمنع منعاً باتاً اعتماد أي نسبة تشابه رقمية مسبقة (مثل 95%) أو فرض نموذج قياس محدد في الكود، حتى يتم إجراء تجارب قياس عملية على مجموعة بيانات مرجعية (Benchmark Dataset) لشخصيات كرتونية كجزء من أبحاث المرحلة الرابعة.

---

## 4. دورة حياة الأصول (Asset Lifecycle)

1. **الاستيراد وفحص الترخيص الأولي (Ingest & License Status Tagging):** حفظ الملف، توليد بصمة الـ SHA-256، وتصنيف حالة الترخيص (`verified`, `pending_review`).
2. **إعادة الاستخدام ومنع التكرار (Deduplication):** منع تكرار حفظ أو توليد نفس الملف، واسترجاعه من الذاكرة المحلية تلقائياً.
3. **الأرشفة المستقلة للقناة:** أصول كل قناة تظل معزولة منطقياً داخل مجلد القناة مع سجل تراخيصها وسجل التدقيق المستقل.
