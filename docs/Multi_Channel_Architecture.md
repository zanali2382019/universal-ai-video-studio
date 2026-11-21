# معمارية إدارة القنوات المتعددة (Multi-Channel Architecture)
## استوديو الإنتاج المرئي بالذكاء الاصطناعي | Universal AI Video Production Studio
**تاريخ الوثيقة:** 16 نوفمبر 2026  
**الحالة:** مواصفة معمارية محدثة للمرحلة الثانية  

---

## 1. نموذج العزل المنطقي المنظم (Structured Logical Isolation Model)

> [!IMPORTANT]
> **توضيح منهجي حول طبيعة العزل في النسخة الأولية (MVP):**  
> العزل المعتمد للقنوات في هذه المرحلة هو **عزل منطقي منظم ومحكم برمجياً (Structured Logical Isolation)**، وليس عزلاً مادياً (Physical Isolation الذي يتطلب خوادم أو أجهزة عتادية منفصلة لكل قناة).  
> يتحقق هذا العزل المنطقي عبر مسارات مجلدات هرمية مستقلة لكل قناة، وفصل استعلامات وسجلات قاعدة البيانات، وضوابط وصول صارمة داخل طبقة معالجة البيانات تمنع تسريب أو اختلاط الأصول والمشروعات بين القنوات المختلفة.

```mermaid
graph TD
    subgraph Universal Video Studio Runtime
        GUI[واجهة الاستخدام المحلية Minimal GUI]
        CoreEngine[Headless CLI Core Engine]
        ChannelManager[مدير سياق القنوات Channel Context Manager]
    end

    subgraph Logical Storage Scoping
        subgraph Channel Scope A: DreamMatter [3D Animation Pilot]
            A_Config[channel_profile.json]
            A_Assets[مجلد أصول وتراخيص القناة A]
            A_Projects[مشاريع القناة A]
        end

        subgraph Channel Scope B: History Pulse [Documentary]
            B_Config[channel_profile.json]
            B_Assets[مجلد أصول وتراخيص القناة B]
            B_Projects[مشاريع القناة B]
        end

        subgraph Channel Scope C: Tech Bites [News / Tech]
            C_Config[channel_profile.json]
            C_Assets[مجلد أصول وتراخيص القناة C]
            C_Projects[مشاريع القناة C]
        end
    end

    GUI --> ChannelManager
    CoreEngine --> ChannelManager
    ChannelManager -->|استعلام موجه فقط| Channel Scope A
    ChannelManager -->|استعلام موجه فقط| Channel Scope B
    ChannelManager -->|استعلام موجه فقط| Channel Scope C
```

---

## 2. هيكل بيانات ملف تعريف القناة (Channel Profile Schema)

يحتوي ملف `channel_profile.json` لكل قناة على حقول التكوين المحدثة:

```json
{
  "channel_id": "ch_dreammatter_001",
  "name": "DreamMatter",
  "handle": "@DreamMatterWorld",
  "niche": "3d_animation_comedy",
  "default_language": "en",
  "supported_locales": ["en", "es", "ar"],
  "compliance": {
    "coppa_default": "not_made_for_kids",
    "synthetic_media_declaration": true
  },
  "branding": {
    "watermark_path": "branding/watermark.png",
    "watermark_position": "top_right",
    "primary_font": "branding/fonts/CustomFont-Bold.ttf",
    "primary_color": "#FFC800",
    "outro_clip_path": "branding/outro.mp4"
  },
  "supported_aspect_ratios": ["9:16", "1:1", "16:9"],
  "default_aspect_ratio": "9:16",
  "defaults": {
    "template_id": "tmpl_3d_cartoon_slapstick",
    "tts_voice_id": "elevenlabs_voice_123",
    "generation_mode": "image_to_video",
    "target_platform_presets": ["youtube_shorts", "tiktok"]
  },
  "active": true
}
```

---

## 3. التنظيم الهيكلي في مسارات الملفات (Filesystem Scoping)

لضمان العزل المنطقي الكامل وسهولة أخذ نسخ احتياطية لكل قناة على حدة:

```
workspace/
├── channels/
│   ├── ch_dreammatter_001/
│   │   ├── channel_profile.json      # تكوين القناة والهوية
│   │   ├── branding/                 # شعارات، خطوط، شاشات
│   │   ├── templates/                # قوالب مخصصة حصراً للقناة
│   │   ├── characters/               # مراجع الشخصيات المخصصة للقناة
│   │   ├── media_vault/              # وسائط ومؤثرات مرخصة للقناة
│   │   │   └── licenses.json         # سجل تراخيص أصول القناة
│   │   └── projects/                 # مشاريع القناة المعزولة منطقياً
│   │       └── proj_20261116_001/
│   │           ├── script.json
│   │           ├── raw_generations/
│   │           └── output/
│   │
│   └── ch_history_pulse_002/
│       ├── channel_profile.json
│       ├── branding/
│       ├── media_vault/
│       │   └── licenses.json
│       └── projects/
│
├── global_templates/                 # قوالب المنصة العامة المشتركة
└── config/
    └── system_settings.json          # الإعدادات العامة للمنصة
```

---

## 4. ضوابط منع التسريب واختلاط البيانات (Anti-Leakage Controls)

1. **التحقق من السياق النشط (Channel Context Guard):**
   * لا يمكن لأي عملية داخل النواة أو الواجهة الرسومية البدء دون تمرير كائن سياق صريح (`ChannelContext`).
   * جميع مسارات الملفات تُبنى نسبياً من جذر مجلد القناة النشطة (`channels/{active_channel_id}/`)، مع حظر استخدام مسارات الـ Absolute أو تجاوز المسار للأعلى (`../`).
2. **عزل استعلامات قاعدة البيانات (Query Scoping):**
   * كل جدول مشاريع ومهام يحتوي على عمود إلزامي ومفهرس `channel_id`.
   * كل استعلام صادر من الواجهة أو المحرك يجب أن يتضمن شرط التصفية الصارم `WHERE channel_id = :active_channel_id`.
3. **تبديل السياق في واجهة الاستخدام (Minimal GUI Context Switching):**
   * عند قيام المستخدم باختيار قناة من القائمة المنسدلة في الواجهة، يُعاد تحميل فضاء العمل بالكامل، وتُفرغ الذاكرة المؤقتة (Cache Clear) لمنع ظهور أي وسائط أو نصوص من القناة السابقة.
