# Grounded — دليل البداية السريعة (العربية)

> هذه ترجمة مختصرة. [الوثائق الإنجليزية](https://github.com/gonisulaimann/Grounded#readme) هي المرجع المعتمد.

**Grounded** جدار حماية لسلامة المراجع: يجد التعليقات وأمثلة التوثيق والاستيرادات وسلاسل الإعداد التي تناقض المستودع — أسماء دوال لم تعد موجودة، وملفات مفقودة، واستيرادات مكسورة، وأمثلة لم تعد صحيحة. حتمي، يعمل دون اتصال، بدون أي اعتماديات. يدعم Python وJavaScript/TypeScript وGo وC.

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
| `stale-entrypoint` | lie | سكربت في `pyproject` أو bin/main في `package.json` يشير إلى شيء غير موجود في المستودع |
| `stale-mock-ref` | lie | نص `@patch` يسمّي رمزًا غير موجود في الوحدة |
| `unclosed-fence` | lie | سياج Markdown لا يغلقه العارض — ما بعده يُعرض ككود |

ثماني من هذه القواعد الـ13 تعمل افتراضيًا؛ والخمس الأخرى اختيارية
(`--enable <id>`).

## الروابط

- [التوثيق الكامل](https://grounded.readthedocs.io/en/latest/) (بالإنجليزية)
- [الإبلاغ عن المشاكل](https://github.com/gonisulaimann/Grounded/issues)
- الرخصة: MIT
