# Template Marketplace — ข้อตกลงจาก grill-me

วันที่: 2026-09-20 | Branch: `feat/frontend-contract-sync`

สถานะ: ผู้ใช้อนุมัติให้ลงมือแล้ว กำลัง implement ตาม implementation ledger
ยังไม่ได้ train หรือ deploy; ผลผ่านต้องมีหลักฐานการทดสอบและ independent review

## 1. ผลลัพธ์ที่ต้องส่งมอบ

ทำ P1 ใน `FRONTEND_MOCK_DATA_BACKEND_HANDOFF.md` ให้ backend ใช้งานได้จริง:
catalog → สร้างโปรเจกต์พร้อมข้อมูลฝึก → training/evaluation/inference ใช้ค่าจริง
→ rating/forks จากกิจกรรมจริง → OpenAPI และตัวอย่างให้ทีม frontend เชื่อมต่อ

แก้โค้ดเฉพาะ backend repo นี้ ส่วน `smart-model-tune/` อ่านอ้างอิงเท่านั้น
ทีม frontend เป็นผู้แก้และ deploy UI รวมถึงปุ่ม rating, การ refresh ตัวเลข,
prefill wizard, การแสดง “ยังไม่มีคะแนน”/“ยังไม่พร้อม” และนำข้อความยอดใช้งานปลอมออก
แยกสถานะ backend สำเร็จออกจากสถานะ frontend เชื่อมเสร็จอย่างชัดเจน

ไม่ทำ landing-page Run Demo จริง (P2), หน้าจอจัดการ template, ระบบ login ใหม่,
งาน SDG อัตโนมัติ, HPO อัตโนมัติ หรือการฝึก template ที่ข้อมูลยังไม่พร้อม

## 2. คำตอบที่ตกลงแล้ว

| เรื่อง | ข้อตกลง |
|---|---|
| ผู้จัดทำ template | `SLM Studio Team`; เครดิต/สิทธิ์ dataset แยกตามแหล่งจริง |
| Catalog | ทีมแก้ curated file แล้ว deploy; ไม่มี CRUD template สำหรับผู้ใช้ |
| เปิดใช้ | เฉพาะ Thai NER `tpl-004` และ Thai Sentiment `tpl-006` หลังลงทะเบียนข้อมูลสำเร็จ อีก 6 รายการแสดง “ยังไม่พร้อม” พร้อมเหตุผล |
| โมเดลตั้งต้น | Qwen2.5-1.5B-Instruct; ใช้ ID ที่ backend รองรับจริง `unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit` |
| สำเนาข้อมูล | แยก DB rows และไฟล์ MinIO ต่อโปรเจกต์ ไม่ใช้ object ต้นฉบับร่วมกัน |
| การใช้ข้อมูล | ฝึกจากข้อมูลที่เตรียมไว้โดยตรง ไม่เรียก SDG และไม่เริ่ม Train เมื่อสร้างโปรเจกต์ |
| Splits | สำเนา train / validation / test แยกให้; validation ใช้ระหว่างฝึก ส่วน test ใช้ประเมินสุดท้าย |
| จำนวนข้อมูล | ค่าเริ่มต้นใช้ train ทั้งหมด; ผู้ใช้เลือกจำนวนไม่เกินที่มีได้ ไม่รวม validation/test |
| การสุ่มลด train | ทำซ้ำได้ด้วย seed ที่บันทึกไว้; classification รักษาสัดส่วนคลาส |
| ค่า override | ผู้ใช้ปรับโมเดลที่รองรับ, prompt, epochs, learning rate และจำนวน train ก่อนเริ่มฝึกได้ |
| เวอร์ชัน | โปรเจกต์เก่าคง snapshot เดิม; version ใหม่มีผลกับโปรเจกต์ใหม่ ไม่เพิ่มปุ่มอัปเดตย้อนหลังในรอบนี้ |
| Prompt | เก็บ snapshot ต่อ training/model version และใช้เป็นค่าเริ่มต้นสอดคล้องกันใน train/evaluate/inference ผ่านระบบเรา |
| ลบโปรเจกต์ | ไม่ลบ dataset, training history หรือโมเดล; เจ้าของเดิมยังต้องเข้าถึงได้ |
| Rating | 1–5 ดาว หนึ่งคะแนนต่อผู้ใช้ต่อ template แก้คะแนนเดิมได้; ต้องเคยสร้างโปรเจกต์จาก template สำเร็จ |
| ไม่มีคะแนน | `rating: null`, `rating_count: 0`; ไม่ใส่คะแนน mock |
| Forks | เริ่ม 0; เพิ่มเมื่อสร้างโปรเจกต์พร้อม dataset สำเร็จจริง ไม่ใช่ตอนกด Use Template |
| ประวัติสถิติ | ลบโปรเจกต์แล้วไม่ลด forks และไม่ลบคะแนน/สิทธิ์ให้คะแนนที่ได้มาแล้ว |
| Retry | สร้างซ้ำด้วย idempotency key เดิมต้องไม่เกิดโปรเจกต์/สำเนา/จำนวน forks ซ้ำ |
| Environment | Local สำหรับ CPU/API/DB → vast.ai สำหรับ GPU → slmpc ผ่าน wetty หลังผ่านเกณฑ์ |
| GPU รอบแรก | ฝึกหนึ่งครั้งต่อ template, 2 epochs, รันทีละงาน ไม่ HPO; ไม่ปรับวนเองเมื่อไม่ผ่าน |

## 3. ข้อมูลที่มีจริง

| Template | Train | Validation | Test | การใช้รอบนี้ |
|---|---:|---:|---:|---|
| Thai NER | 4,240 | 500 | 500 | เปิดเมื่อ import/ตรวจสอบครบ |
| Thai Sentiment | 6,000 | 600 | 900 | เปิดเมื่อ import/ตรวจสอบครบ |
| Invoice QA | 317 | 44 | 16 | เก็บเป็น partial; English synthetic ไม่ครบ Thai/English |
| Function Calling | 162 | 17 | 41 | partial; CRUD ไม่ครบ |
| Product QA | 329 | 66 | 72 | partial; IBM support ไม่ใช่ knowledge base ของผู้ใช้ |
| Smart Home | 1,220 | 131 | 158 | partial; ไม่มี alarm |
| Thai Customer Support | — | — | — | ยังไม่มีแหล่งที่ผ่านเงื่อนไขครบ 6 หมวด |
| Medical Triage | — | — | — | ยังไม่มีแหล่งที่ยืนยันได้ครบ 4 ระดับ |

รายละเอียดต้นทาง/สิทธิ์/ข้อจำกัดอยู่ใน `template-training-data.md` และ manifest
ใต้ `data/template-catalog/prepared/` ซึ่ง git-ignore ไว้ ห้าม commit ข้อมูลดิบ
หรือ model weights; ต้องมีสคริปต์ import แบบทำซ้ำได้พร้อมตรวจ SHA-256

จำนวนข้อมูลและการผ่าน schema/tokenizer ไม่ใช่หลักฐานคุณภาพโมเดล
ต้องตรวจความยาว full chat ใหม่เมื่อเพิ่ม prompt หรือเปลี่ยน tokenizer;
ห้ามตัดคำตอบ/บริบทเงียบ ๆ เพื่อให้ผ่าน max sequence length

## 4. แบบ backend ที่เสนอสำหรับ implementation

### Catalog และ contract

- เพิ่ม `GET /api/v1/templates` ตาม field/รูป pagination ใน handoff รองรับ
  category, featured, search, sort, limit, offset โดยไม่เชื่อค่าจาก mock
- เพิ่ม availability/reason, version, split counts, source attribution และ
  rating_count/my_rating เป็น additive fields สำหรับ frontend
- ค่าเริ่มต้นของรายการคืนเฉพาะที่ใช้ได้; frontend ขอ
  `include_unavailable=true` เพื่อแสดงครบพร้อมการ์ด “ยังไม่พร้อม” ได้
- ใช้ auth/Supabase identity ที่มีอยู่สำหรับ catalog และการกระทำของผู้ใช้
  ไม่รับ `owner_id` จาก request และไม่ใช้ IP/anonymous identity แทนผู้ให้คะแนน
- คง task enum เดิม: classification/tool_calling/qa; NER เป็น QA
- เก็บ curated definition/version ในไฟล์; เก็บ registration, usage และคะแนน
  ใน Postgres ไม่เพิ่มหน้าจอ admin หรือระบบ CMS

### สร้างโปรเจกต์และเก็บข้อมูล

- ขยายการสร้างโปรเจกต์เดิมด้วย template reference/version และ overrides แบบ
  optional; การสร้างโปรเจกต์ทั่วไปต้องยังทำงานเหมือนเดิม
- Backend ตรวจ availability, version, task/model compatibility, จำนวน train
  และช่วงค่าฝึกเอง ไม่เชื่อ frontend validation อย่างเดียว
- เก็บ definition/config snapshot และ dataset IDs ของสาม splits ในโปรเจกต์
  frontend ใช้ค่าที่ backend ตอบกลับ ไม่พึ่ง localStorage เป็นแหล่งข้อมูลหลัก
- Object ต้นฉบับเป็น immutable/versioned และไม่อยู่ในเส้นทางที่ผู้ใช้ลบได้
  แต่ละโปรเจกต์ได้สำเนาแยกจริง ชุด validation/test ต้องมี role ชัดเจน
- ป้องกันเลือก validation/test ของ template ไปเป็น training dataset
  การลดจำนวน train ไม่ลดหรือสุ่ม validation/test ใหม่
- ใช้ธุรกรรม Postgres กับ unique constraint สำหรับ durable idempotency/usage
  helper Redis เดิมเป็น best-effort 60 วินาที จึงไม่พอสำหรับรับประกัน forks
- Key เดิม + payload เดิมคืนผลเดิม; key เดิม + payload ต่างกันต้อง reject
  ความผิดพลาด/การเรียกพร้อมกันต้องไม่ทิ้งโปรเจกต์ครึ่งเดียวหรือเพิ่ม forks
- MinIO และ Postgres ไม่ใช่ transaction เดียวกัน: เตรียมสำเนาให้ครบก่อน
  commit ความสำเร็จ แล้ว cleanup เฉพาะ object ที่ request นั้นสร้างเมื่อ fail;
  มีแนวทางตรวจ/เก็บกวาดไฟล์ที่ค้างหาก process ตาย โดยไม่แตะไฟล์คนอื่น
- ใช้ count จาก durable successful-use records เป็น forks และ aggregate
  คะแนนปัจจุบันจาก DB; ไม่เพิ่ม counter ที่ต้องคอย sync ถ้าไม่จำเป็น

### Rating

- เพิ่ม upsert คะแนนของผู้ใช้ปัจจุบันด้วย integer 1–5 พร้อม unique
  `(template_id, user_id)` และตรวจประวัติการสร้างโปรเจกต์สำเร็จจาก DB
- บันทึกคะแนน/ประวัติการใช้แยกจาก project FK ที่จะถูกลบ จึงไม่หายตามโปรเจกต์
- Response หลังบันทึกคืน average/count/my_rating ล่าสุด; อ่านครั้งถัดไปเห็น
  ค่าที่ commit แล้ว ไม่มี mock หรือรอ background aggregation
- Frontend invalidate/refetch หลัง mutation; ไม่เพิ่ม WebSocket สถิติใหม่ใน
  รอบนี้ และไม่อ้างว่าหน้าของผู้ใช้อื่นจะ push-update โดยไม่ refetch

### Training, evaluation และ prompt

- เมื่อรับงานฝึก snapshot ค่าที่ effective จริง: model, prompt, parameters,
  dataset split IDs/hash และ template version; การแก้โปรเจกต์ภายหลังไม่เปลี่ยนงานนั้น
- ส่ง validation ที่เตรียมไว้เข้า trainer โดยตรง ไม่ใช้ `train_test_split`
  ซ้ำในเส้นทาง template; เส้นทางเก่าที่ไม่ได้ส่ง external validation ยังคงเดิม
- ใช้ train เท่านั้นในการปรับ weights และ validation ในการเลือก checkpoint;
  test ต้องไม่ถูกใช้เป็น training/validation หรือใช้ปรับค่าซ้ำ
- Prompt ต้องผ่านตั้งแต่ request/DB → worker → formatter → evaluation และ
  default inference ของ artifact; คงพฤติกรรมเดิมเมื่อไม่มี custom prompt
- ใช้ snapshot ของโมเดล ไม่อ่าน prompt ล่าสุดจาก template/โปรเจกต์ตอน infer
  explicit prompt ที่ผู้เรียกส่งให้ inference ยังคงเป็น override ที่ชัดเจน
- Metadata/export handoff ต้องระบุ prompt และ chat template; ไม่อ้างว่าไฟล์
  weights อย่างเดียวบังคับ external runtime ทุกตัวให้ใช้ prompt เดียวกันได้
- เคารพขอบเขต hexagonal/Celery และ API/model allowlist เดิม ไม่เพิ่ม task type

### การลบและ ownership ที่ต้องแก้ให้ตรงคำตอบข้อ 8

ตรวจพบว่าระบบเดิมเก็บ training/model หลังลบ project แต่ authenticated owner
เข้าไม่ถึง เพราะ ownership ยังผ่าน Project เท่านั้น จึงต้องเก็บ owner ของ
TrainingJob แยกจาก Project เช่นเดียวกับ Dataset แล้วให้ model/evaluation
อ้างเจ้าของจาก training ที่ยังอยู่ ตรวจทุก list/detail/download/inference path

Backfill เฉพาะรายการที่พิสูจน์ owner จาก Project ที่ยังอยู่ได้ รายการเก่าที่
ไม่มีหลักฐานเจ้าของต้อง fail closed ต่อไป ห้ามเดาเจ้าของหรือเปิด public
ไม่เปลี่ยนการลบโปรเจกต์เป็น cascade และไม่เปลี่ยนสิทธิ์ผู้ใช้อื่น

## 5. การทดสอบและเกณฑ์ผ่าน

### Local — ไม่ต้องมี GPU

- Catalog schema/filters/pagination/auth และ availability ตรงข้อมูลที่ลงทะเบียน
- ทดสอบกับ Postgres/MinIO จริงสำหรับ migration/import/copy/rollback ไม่ใช้
  SQLite เพียงอย่างเดียวพิสูจน์ธุรกรรมและ concurrency
- Retry/concurrent create ไม่เพิ่มโปรเจกต์/usage/forks ซ้ำ; payload conflict
  และ MinIO failure ไม่สร้างผลสำเร็จปลอม
- คะแนน null → คะแนนจริง → แก้คะแนน, eligibility, ownership และผู้ใช้อื่น
- สำเนาแยกจากต้นฉบับ; split roles, counts/hash, sampling reproducible และ
  snapshot เก่าคงเดิมหลังแก้ catalog
- Prompt/external validation วิ่งครบ contract ทั้ง API/worker/trainer/serving;
  missing defaults ต้องไม่ทำให้ workflow เก่าพัง
- ลบโปรเจกต์แล้วยังเข้าถึงทรัพยากรของตัวเอง ไม่เห็นของผู้อื่น; forks/ratings คงอยู่
- Export OpenAPI และส่งตัวอย่าง success/error ให้ frontend ไม่แก้ frontend เอง
- รายงาน baseline failures เดิมแยกจาก regression ใหม่ ห้ามเรียกรวมว่า all green

### vast.ai — ผู้ใช้ส่ง SSH host/port หลัง grill

- ตรวจรุ่น GPU/VRAM/driver/storage ก่อนเริ่ม ไม่สมมติว่าเครื่องเก่าใน hub ยังอยู่
- ใช้ branch/artifact version ที่ผ่าน Local; ไม่เช่าเครื่องหรือสร้างค่าใช้จ่าย
  ใหม่เอง และไม่ส่งข้อมูลไป SDG/OpenRouter อัตโนมัติ
- Qwen 1.5B, manual training 2 epochs หนึ่งครั้งต่อ template รันทีละงาน
  ค่าอื่นใช้ค่าปลอดภัยของ backend; บันทึก seed/config/data hashes/prompt
- ทดสอบ Train → Evaluate → Export → Inference ทั้งสอง template และตรวจ
  ชุด validation ถูกใช้จริง ไม่ใช่ internal random split ที่มาแทนเงียบ ๆ
- เทียบ base และ finetuned model บน test เดียวกันด้วย preprocessing/prompt/
  decoding settings ที่สอดคล้องกัน ใช้ final artifact ที่จะ serve จริง
- Sentiment ใช้ Macro-F1; NER ใช้ exact entity type + start/end span micro-F1
  พร้อมรายงาน precision/recall และอัตราคำตอบ JSON ผิดรูป ไม่ใช้ QA ROUGE แทน
- ต้องผ่านเส้นทางการทำงาน และคะแนนหลักหลังฝึกไม่ต่ำกว่า base สำหรับทั้งสองชุด
  คะแนนเท่าฐานไม่ใช่หลักฐานว่าปรับปรุงขึ้น; รายงาน absolute scores ด้วย
- ถ้าคุณภาพแย่ลง/งานฝึกล้มเหลว ให้หยุดและเสนอทางเลือก ไม่ฝึกวนหรือปรับตาม test
  เอง การทดลองเพิ่มต้องขอผู้ใช้ก่อน ไม่ขยายเป็น HPO อัตโนมัติ
- นำผลตรวจ/config/checksums กลับมาเก็บ local ไม่ฝากหลักฐานชุดเดียวบนเครื่องเช่า

### Deploy

หลังผ่าน Local + vast.ai แล้วเท่านั้น จึง deploy backend ไป
`https://slmpc.pasaflow.com/wetty` ตาม `deploy-slm-vm` runbook ตรวจสถานะจริง/
backup/migration/image version และ import dataset ต้นฉบับก่อนเปิด availability
ไม่คัดลอกคะแนน/forks จากบัญชีทดสอบหรือทั้ง DB ของ vast.ai ไป production

ตรวจ health/readiness/auth/template API/registration หลัง deploy อีกครั้ง
การทดสอบบน vast.ai ไม่ยืนยัน GPU ของ slmpc ต้องตรวจ hardware/runtime ของ
ปลายทางแยก และแยกผลทดสอบ API หลัง deploy จากการฝึก GPU ที่ผ่านบน vast.ai

## 6. วิธีลงมือหลังตรวจ spec

ใช้ `delegate-build`: planner แบ่ง ordered waves ที่ไม่ชนไฟล์ → agents ทำ
ขนานในแต่ละ wave → independent reviewer ตรวจทั้งข้อกำหนดและ tests จริง
ใช้ `ponytail`: ใช้ service/schema/helper เดิมก่อน ไม่เพิ่ม framework/CMS/
generic workflow engine และไม่ตัด validation/security/tests ที่จำเป็น

Session นี้ไม่มี Opus/Sonnet ให้เลือก จึงใช้ Codex agents แยกบทบาท planner,
implementer และ reviewer โดยแจ้งข้อจำกัดตรง ๆ หาก review FAIL ต้องแจ้ง gap
และกลับมาถามเฉพาะประเด็นที่ไม่ผ่านก่อนทำรอบใหม่

ข้อจำกัดปัจจุบัน: ยังไม่ได้รับ SSH ของ vast.ai, ยังไม่ได้ตรวจสถานะ slmpc
ในรอบนี้ และ frontend wiring ต้องทำโดยทีม frontend จึงยังอ้าง end-to-end
production completion ไม่ได้แม้ Local tests จะผ่าน
