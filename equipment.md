# Lynx Fitness Gym — Equipment Inventory

Source: user's photo tour (2026-04-21, IMG_0577–IMG_0613).

## Barbells & Specialty Bars
- Olympic barbells (multiple, with Rogue power rack + rig)
- EZ curl bar
- Trap/hex bar
- Swiss (football) bar
- Other specialty bars on rack (IMG_0581, IMG_0586)

## Plates
- Rubber/bumper plates on Rogue rig

## Dumbbells
- Hex dumbbells, ~20–110 lb (5 lb increments, IMG_0577)

## Racks & Benches
- Rogue power rack (IMG_0584)
- Rogue rig / cage with pull-up bars (IMG_0585, IMG_0609)
- Outdoor-turf platform with Rogue platform (IMG_0579)
- Flat / adjustable benches (IMG_0595, IMG_0608)
- Preacher curl bench (IMG_0583)
- GHD / hyperextension-style bench (IMG_0599)

## Smith Machines
- Smith machine — supports both flat and incline bench setups. Bar weight ~15–20 lb (not 45). Used 2026-04-21 for Incline Bench Press (template 3A6FA3D1).

## Plate-Loaded / Selectorized Machines

### Chest
- Hammer Strength Vertical Chest Press (IMG_0606)
- Pec deck / chest fly machine (confirmed present)

### Back
- Hammer Strength Iso-Lateral Row, plate-loaded, 12 lb starting resistance (IMG_0588)
- Life Fitness Low Row, selectorized (IMG_0582, IMG_0587)
- Life Fitness Lat Pulldown (IMG_0602)
- Life Fitness Dual Pulley Row (IMG_0596)

### Shoulders
- No dedicated shoulder press machine. Use DBs, barbell OHP, Smith, or cables.

### Legs
- Hammer Strength Linear Leg Press (IMG_0610)
- Hammer Strength Hack Squat, plate-loaded (IMG_0608)
- Hammer Strength Leg Extension (IMG_0600)
- Hammer Strength Seated Leg Curl (IMG_0605)
- Hammer Strength Seated Calf, 60 lb starting resistance (IMG_0589)
- Matrix Hip Adductor/Abductor (IMG_0604)

### Arms
- Preacher curl bench (IMG_0583)
- Life Fitness Triceps Pushdown station (IMG_0601)

## Cables
- Life Fitness Adjustable Cable Crossover (IMG_0592, IMG_0603)
- Life Fitness Dual Adjustable Pulley (IMG_0593, IMG_0598)
- Genesis Dual Cable Cross Lite (IMG_0597)
- Attachments: full selection available (V-grip, rope, straight bar, EZ bar, single handles, ankle strap, lat bar, etc.)

## Accessories
- Medicine balls / wall balls (multiple sizes, IMG_0591, IMG_0594)
- Battle ropes (IMG_0591)
- Resistance bands (IMG_0590)

## Cardio
- Assault air bike (IMG_0580)
- Upright / spin bikes (IMG_0611)
- Curved/self-powered treadmills (IMG_0612)

---

## Equipment ↔ Exercise Conversions (for generator)

Literature-based coefficients. Always treat as starting estimates; adjust by RPE.

### Bench press: Barbell ↔ Smith
Cotterman et al. 2005 (J Strength Cond Res) regressed 1RM across free-weight and Smith bench in trained lifters:

    SM_bench_1RM_kg ≈ 0.95 × BB_bench_1RM_kg − 6.76

In lb, roughly: `SM ≈ 0.95 × BB − 15`. Smith bench is ~5–10% lighter than BB at typical working loads, *plus* whatever the Smith bar itself weighs (see `profile.smith_bar_weight_lb` — the Lynx Smith is counterweighted, not 45 lb).

### Squat: Barbell ↔ Smith
Cotterman 2005 also measured squat: in **men**, no significant difference between Smith and free squat 1RM. In **women**:

    SM_squat_1RM_kg ≈ 0.73 × BB_squat_1RM_kg + 28.3

For male users, start 1:1 and tune.

### Barbell ↔ DB pair (pressing)
Saeterbakken et al. 2011 (J Strength Cond Res), bench press 6RM:

    BB_total ≈ 1.17 × DB_pair_total        (DB pair ≈ 83% of BB)

So a 225 BB bench ≈ 192 lb total DB pair ≈ 96 lb per hand. For overhead press the ratio is similar or slightly larger (DBs harder to stabilize overhead).

### Machine ↔ free weight
Machine plates and pulley ratios vary by manufacturer (Hammer Strength vs Life Fitness vs Matrix all differ). No clean coefficient. Rule: start 20% lower than free-weight equivalent, adjust within the first 2 sessions.

### Cable ↔ DB (isolation)
Cable has constant tension across the ROM; DBs have a strength curve. Empirically cable working weight is ~10–20% lower than DB at the same rep count for curls, laterals, flies.

### Summary table

| From → To         | Formula / starting point                 | Source |
|-------------------|------------------------------------------|--------|
| BB bench → Smith  | `SM_lb ≈ 0.95·BB_lb − 15` (+ Smith bar)  | Cotterman 2005 |
| BB squat → Smith (M) | ≈ 1:1                                 | Cotterman 2005 |
| BB squat → Smith (F) | `SM_kg ≈ 0.73·BB_kg + 28.3`           | Cotterman 2005 |
| BB press → DB pair | `DB_pair ≈ 0.83 × BB` (÷2 per hand)     | Saeterbakken 2011 |
| DB pair → BB       | `BB ≈ 1.17 × DB_pair`                   | Saeterbakken 2011 |
| Machine → BB/DB    | start 20% lower, tune by RPE            | heuristic |
| DB → Cable (iso)   | subtract 10–20%                         | heuristic |

**References**
- Cotterman ML, Darby LA, Skelly WA. *Comparison of muscle force production using the Smith machine and free weights for bench press and squat exercises.* J Strength Cond Res. 2005;19(1):169-176.
- Saeterbakken AH, van den Tillaar R, Fimland MS. *A comparison of muscle activity and 1-RM strength of three chest-press exercises with different stability requirements.* J Sports Sci. 2011;29(5):533-538.

## Unavailable / Often-Occupied
- (log here when machines are taken so generator can deprioritize them)
