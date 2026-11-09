# Turnip A840 OnePlus 15 Apex (Mesa 26.3 / Android 17)

Единая флагманская сборка Vulkan-драйвера Turnip (`libvulkan_freedreno.so`) специально для **OnePlus 15 (Snapdragon 8 Elite Gen 5 / SM8850 / Adreno 840 GPU / Oryon Gen 3 CPU)** под **Android 17 (API 37)** из ветки `staging/26.3`.

## Ключевые технологии (High-IPC & Race-to-Idle)
- **IR3 Gen8 ALU Latency Unlock (`ir3_compiler.c`)**: исправляет проверку `compiler->gen == 7` на `compiler->gen >= 7`, активируя задержки `2/5/1` вместо устаревших `3/6/2` (A6xx) и убирая 33–50% пустых инструкций `nop` между зависимыми ALU-операциями.
- **IR3 Deep 16-Fetch Texture Window (`ir3_sched.c`)**: расширяет очередь одновременных выборок текстур/памяти `(sy)` с `8` до `16` и `(ss)` с `8` до `12` для 128-векторного регистрового файла Adreno 840 + минимизация регистрового давления (`choose_instr_inc`, Vauzi-17 #63).
- **DXVK Mutable-Format UBWC Unlock (`tu_image.cc`)**: сохраняет аппаратное сжатие полосы пропускания **UBWC** на текстурах и рендер-таргетах с флагом `VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT` в играх на DXVK (*Muck*, *Dying Light* и др.) + тайлинг `TILE6_3` для `DRM_FORMAT_MOD_QCOM_COMPRESSED` (Turnip-Z #30).
- **18 MB HPM Zero-CPU Fast-Path GMEM (`tu_cmd_buffer.cc` & `tu_autotune.cc`)**: рендер-пассы, помещающиеся в `<= 2` тайла 18 МБ HPM или содержащие `>= 8` вызовов отрисовки, направляются напрямую в накристальную память GMEM/HPM без накладных расходов CPU на хэширование autotune и без записи GPU timestamp-пакетов.
- **Smart A2C Gate (`tu_pipeline.cc`) & Game Profiles (`00-turnip-defaults.conf`)**: блокирует тяжелую шейдерную эмуляцию Alpha-to-Coverage на 1x MSAA и активирует аппаратный A2C + `bandwidth` autotune для `DXVK|vkd3d`, `Unity.*`, *Muck* и *Dying Light*.
- **Плоский архив без вложенности**: как в GitHub Releases, так и во вкладке GitHub Actions Artifacts формируется готовый к установке в AdrenoTools плоский ZIP-архив (`libvulkan_freedreno.so` + `meta.json` в корне, без «архива в архиве»).
