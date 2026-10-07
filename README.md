# EOSB Provision — مخصص نهاية الخدمة ورصيد الإجازات

App لـ ERPNext v15 + HRMS بيحجز مخصص نهاية الخدمة ورصيد الإجازات لكل موظف، وبيعمل القيد بالفرق بس.

## التركيب
```bash
cd ~/frappe-bench
bench get-app /path/to/eosb_provision        # أو رابط GitHub بعد ما ترفعه
bench --site <site> install-app eosb_provision
bench --site <site> migrate
bench restart
```

## الإعداد (مرة واحدة)
1. **EOSB Provision Settings**
   - Gratuity Rule: الـ Rule اللي هتتحسب بيها (المكونات، الشرائح، عدد الأيام، طريقة Work Experience، أنواع الإجازات).
   - Employees with Relieving Date: `Exclude` (أول ما يتحط تاريخ ترك المخصص بيقف) أو `Include until Relieving Date`.
   - Deduct Absent / Leave Without Pay Days: يخصم أيام الغياب والإجازات بدون أجر من مدة الخدمة.
   - Company Accounts: لكل شركة حساب مصروف ومخصص الإجازات، و Cost Center، وحسابات نهاية خدمة افتراضية (اختياري).
2. **Salary Component** → جدول **EOSB Provision Accounts**: لكل شركة حساب المصروف وحساب المخصص (يفضل نوعه Payable).
   الحقلين القديمين `custom_gratuity_expense_account` / `custom_gratuity_payable_account` بيتنقلوا للجدول ده تلقائياً عند التركيب.

## الاستخدام
1. **المخصص الافتتاحي (مرة واحدة)**: EOSB Provision جديد ← علّم **Is Opening** ← Get Employees ← اكتب الرصيد القديم الفعلي لكل موظف لو مختلف ← Submit.
   مفيش قيد بيتعمل. ده بيبقى الأساس.
2. **كل فترة**: EOSB Provision جديد ← Get Employees ← Submit.
   المستحق الحالي − المحجوز (آخر مخصص، أول مرة = الافتتاحي) = التسوية ← قيد Draft بالفرق.

## القواعد
- الموظفين الـ Active بس.
- الراتب = مكونات الـ Rule من آخر Salary Slip عاملة Submit. ولو المرتب متقسم على كذا مكوّن، المستحق بيتوزع عليهم بالنسبة وكل مكوّن بيتقيد على حساباته.
- لو موظف اتشال منه مكوّن (اتنقل إدارة)، بيتعمل سطر عكس للمكوّن القديم.
- موظف مالوش Slip بيتخطى، ورصيده المحجوز زي ما هو.
- مينفعش تعمل Cancel لقيد جاي من المخصص. اعمل Cancel للمخصص نفسه.
- مينفعش تعمل Cancel لمخصص بعده مخصص Submitted، ولا مخصص بتاريخ أقدم من آخر مخصص.

## التقرير
**EOSB Provision Balance**: لكل موظف بتاريخ: المستحق، والمحجوز، واللي لسه ماتقيدش، ورصيد الإجازات، ورصيد الـ GL بالموظف. والموظفين اللي سابوا بيظهروا برصيدهم المحجوز.
