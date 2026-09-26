# DO NOT EDIT: generated from packages/contracts/schemas by `make contracts`.
# Specialised facts narrow a base field (e.g. unit: Literal["Cel"] over str); Pydantic validates
# the narrowed type, so pyright's invariance check on mutable fields is disabled for this file.
# pyright: reportIncompatibleVariableOverride=false
#
# source: packages/contracts/alert_templates.yaml
"""Fixed, non-PHI alert display templates and required-action keys per rule_id."""

from typing import Final

TEMPLATES_VERSION: Final = 1

ALERT_TEMPLATES: Final[dict[str, dict[str, str]]] = {
    'FAST_STROKE': {
        'en': 'Possible stroke: FAST screen positive. Immediate stroke assessment.',
        'ar': 'سكتة دماغية محتملة: فحص FAST إيجابي. تقييم فوري للسكتة الدماغية.',
    },
    'CHEST_PAIN_ECG_10MIN': {
        'en': 'Chest pain: ECG within 10 minutes of arrival.',
        'ar': 'ألم في الصدر: تخطيط القلب خلال 10 دقائق من الوصول.',
    },
    'SEPSIS_NEWS2': {
        'en': 'Possible sepsis: suspected infection with NEWS2 5 or more. Urgent review.',
        'ar': 'إنتان محتمل: اشتباه بعدوى مع NEWS2 بقيمة 5 أو أكثر. مراجعة عاجلة.',
    },
    'NG143_RED_FLAG': {
        'en': 'Child red flag (NICE NG143). Urgent medical review.',
        'ar': 'علامة خطر حمراء لدى الطفل (NICE NG143). مراجعة طبية عاجلة.',
    },
    'NG143_AMBER_FLAG': {
        'en': 'Child amber flag (NICE NG143). Prompt clinical assessment.',
        'ar': 'علامة تحذير برتقالية لدى الطفل (NICE NG143). تقييم سريري سريع.',
    },
    'CHILD_HARD_TO_WAKE': {
        'en': 'Child hard to wake. Urgent medical review.',
        'ar': 'يصعب إيقاظ الطفل. مراجعة طبية عاجلة.',
    },
}

REQUIRED_ACTIONS: Final[dict[str, str]] = {
    'FAST_STROKE': 'stroke_team_now',
    'CHEST_PAIN_ECG_10MIN': 'ecg_within_10_min',
    'SEPSIS_NEWS2': 'sepsis_review_now',
    'NG143_RED_FLAG': 'paediatric_review_now',
    'NG143_AMBER_FLAG': 'paediatric_assessment_soon',
    'CHILD_HARD_TO_WAKE': 'paediatric_review_now',
}

ALERT_QUALIFIERS: Final[dict[str, dict[str, str]]] = {
    'parent_unsure': {
        'en': 'parent unsure',
        'ar': 'ولي الأمر غير متأكد',
    },
    'partial_news2': {
        'en': 'partial NEWS2 (kiosk vitals only)',
        'ar': 'NEWS2 جزئي (قياسات الكشك فقط)',
    },
}
