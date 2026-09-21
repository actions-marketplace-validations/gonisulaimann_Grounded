# Grounded — دليل البداية السريعة (العربية)

> هذه ترجمة مختصرة. [الوثائق الإنجليزية](../README.md) هي المرجع المعتمد.

**Grounded** يجد المراجع المعلّقة في تعليقات الكود: أسماء دوال لم تعد موجودة، وملفات مفقودة، واستيرادات مكسورة. حتمي، يعمل دون اتصال، بدون أي اعتماديات. يدعم Python وJavaScript/TypeScript وGo وC.

## التثبيت

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

## الاستخدام

```console
grounded scan .                    # فحص المستودع (رمز الخروج 1 عند وجود أخطاء)
grounded scan . --changed          # فقط الأسطر المعدّلة (مثالي لسير CI)
grounded fix . --dry-run           # معاينة الإصلاحات قبل تطبيقها
grounded impact my_function .      # كل ما يرتبط بالرمز: أين عُرّف، من يستورده
```

## القواعد

| المعرف | الخطورة | ماذا يفحص |
|---|---|---|
| `stale-symbol-ref` | lie | تعليق يسمّي دالة غير موجودة في المستودع |
| `stale-import` | lie | استيراد قابل للحلّ لوحدة مفقودة أو اسم غير معرّف فيها |
| `stale-file-ref` | lie | تعليق يشير إلى مسار غير موجود داخل المستودع |
| `number-drift` | drift | رقم في تعليق يخالف الكود المجاور |
| `fragile-anchor` | smell | مراجع أسطر هشّة وعلامات workaround بلا تذكرة |

## الروابط

- [التوثيق الكامل](https://grounded.readthedocs.io/en/latest/) (بالإنجليزية)
- [الإبلاغ عن المشاكل](https://github.com/gonisulaimann/Grounded/issues)
- الرخصة: MIT
