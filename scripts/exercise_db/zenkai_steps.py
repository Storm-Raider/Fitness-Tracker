"""Steps for the curated exercises free-exercise-db can't cover: no true match
(curated_map.py maps them to None) or a match with no instructions.

Written for Zenkai, not from the database, so build.py tags them
steps_source="zenkai" and the detail page says so. Each agrees with the
exercise's own cue in app/data/exercises.py. Level follows the database's
scale: beginner / intermediate / expert."""

ZENKAI_STEPS: dict[str, tuple[str, list[str]]] = {
    "Assault Bike": ("beginner", [
        "Set the seat so your knee is slightly bent at the bottom of the pedal stroke.",
        "Grip the handles at chest height and start pedaling at an easy pace.",
        "Push and pull the handles in rhythm with your legs; the harder you work, the more the fan resists.",
        "For intervals, go all out for 10 to 30 seconds, then pedal easy until your breathing settles.",
    ]),
    "Burpee": ("beginner", [
        "Stand with feet shoulder-width apart.",
        "Squat down and put your hands on the floor in front of your feet.",
        "Jump or step your feet back into a plank and do one push-up.",
        "Jump your feet back toward your hands, landing in a squat.",
        "Drive up and jump, reaching overhead. Land softly and go straight into the next rep.",
    ]),
    "Cable Tricep Kickback": ("beginner", [
        "Set a pulley at its lowest point and hold the handle (or the bare cable end) in one hand.",
        "Step back and hinge forward until your torso is nearly parallel to the floor, free hand on your thigh or a bench.",
        "Bring your working upper arm in line with your torso and keep it pinned there.",
        "Straighten the elbow until the arm is fully extended and squeeze the triceps.",
        "Bend the elbow slowly back to 90 degrees without letting the upper arm drop. Finish the set, then switch arms.",
    ]),
    "Copenhagen Plank": ("intermediate", [
        "Lie on your side next to a bench and prop yourself on your forearm, elbow under your shoulder.",
        "Place the inside of your top foot (or knee, for an easier version) on the bench.",
        "Lift your hips until your body forms a straight line, bottom leg hanging below the bench.",
        "Hold, squeezing your top inner thigh into the bench and keeping the hips from sagging.",
        "Lower with control and switch sides.",
    ]),
    "Donkey Kick": ("beginner", [
        "Start on hands and knees, hands under shoulders and knees under hips.",
        "Keeping the knee bent at 90 degrees, kick one foot up toward the ceiling by squeezing the glute.",
        "Stop when your thigh is in line with your torso; don't arch your lower back.",
        "Hold for a moment at the top, then lower with control. Finish the set and switch legs.",
    ]),
    "Dragon Flag": ("expert", [
        "Lie on a bench and grip it (or a post) behind your head with both hands.",
        "Roll up onto your upper back, lifting your hips and legs until your body is nearly vertical.",
        "Brace hard so your body is one straight, rigid line from shoulders to feet.",
        "Lower your body slowly as a single unit, pivoting at the shoulders, without bending at the hips.",
        "Stop just above the bench, then raise back up the same way.",
    ]),
    "Dumbbell Hip Thrust": ("beginner", [
        "Sit on the floor with your upper back against the edge of a bench and a dumbbell across your hips.",
        "Plant your feet flat, about hip-width apart, so your shins are vertical at the top.",
        "Tuck your chin, hold the dumbbell in place, and drive through your heels to lift your hips.",
        "Stop when your torso is parallel to the floor and squeeze your glutes hard.",
        "Lower your hips under control until they nearly touch the floor.",
    ]),
    "Fire Hydrant": ("beginner", [
        "Start on hands and knees, hands under shoulders and knees under hips.",
        "Keeping the knee bent at 90 degrees, lift one leg out to the side.",
        "Raise it as high as you can without tipping your hips or shifting your weight.",
        "Pause, then lower with control. Finish the set and switch sides.",
    ]),
    "Flutter Kicks": ("beginner", [
        "Lie on your back with your legs straight and your hands under your hips.",
        "Press your lower back into the floor and lift both heels a few inches off the ground.",
        "Kick your legs up and down in small, quick alternating movements.",
        "Keep your lower back pressed down the whole time; if it lifts, raise your legs a little higher.",
    ]),
    "Frog Pump": ("beginner", [
        "Lie on your back and bring the soles of your feet together, knees falling out wide.",
        "Pull your heels in toward your hips and rest your arms by your sides.",
        "Squeeze your glutes and thrust your hips up toward the ceiling.",
        "Pause and squeeze hard at the top, then lower. Use fast, high reps.",
    ]),
    "Hanging Knee Raise": ("beginner", [
        "Hang from a pull-up bar with an overhand grip, arms straight.",
        "Tilt your pelvis back slightly, as if pulling your belt buckle toward your ribs.",
        "Draw your knees up toward your chest, curling your hips up at the top.",
        "Lower your legs slowly until they're straight, without swinging.",
    ]),
    "Hollow Hold": ("intermediate", [
        "Lie on your back with your arms extended overhead and legs straight.",
        "Press your lower back firmly into the floor.",
        "Lift your shoulders, arms and legs a few inches off the floor into a shallow banana shape.",
        "Hold. If your lower back lifts, bend your knees or bring your arms forward to make it easier.",
    ]),
    "Incline Walk": ("beginner", [
        "Start the treadmill at a slow walk with no incline.",
        "Raise the incline gradually to a steep setting (around 8 to 15 percent).",
        "Set a brisk pace you can keep for the whole session.",
        "Stand tall and let your arms swing; don't hold the rails or lean on the console.",
    ]),
    "Jumping Jacks": ("beginner", [
        "Stand with your feet together and arms at your sides.",
        "Jump your feet out wider than your shoulders while raising your arms overhead.",
        "Jump your feet back together while bringing your arms back down.",
        "Stay light on the balls of your feet and keep a steady rhythm.",
    ]),
    "Kettlebell Swing": ("intermediate", [
        "Stand with feet a little wider than shoulder-width, kettlebell on the floor a foot in front of you.",
        "Hinge at the hips, grip the handle with both hands, and hike the bell back between your legs.",
        "Snap your hips forward explosively, standing tall and squeezing your glutes.",
        "Let the bell float to chest height; your arms guide it but don't lift it.",
        "As it falls, hinge again and let it swing back between your legs into the next rep.",
    ]),
    "L-Sit": ("expert", [
        "Place your hands on parallel bars, parallettes or the floor beside your hips.",
        "Press down to straighten your arms and lift your hips, pushing your shoulders down away from your ears.",
        "Raise your legs straight out in front of you until they're parallel to the floor.",
        "Hold with legs together and knees locked. Tuck your knees to make it easier.",
    ]),
    "Landmine Press": ("beginner", [
        "Anchor one end of a barbell in a landmine or a corner and load the other end.",
        "Stand facing the free end, staggered stance, holding the end of the bar at shoulder height.",
        "Brace your core and press the bar up and slightly forward until your arm is straight.",
        "Lower it back to your shoulder under control. Finish the set and switch arms, or press with both hands.",
    ]),
    "Lateral Lunge": ("beginner", [
        "Stand with your feet together, hands at your chest.",
        "Take a wide step to one side and push your hips back, bending the stepping knee.",
        "Keep the other leg straight and both feet flat, chest up.",
        "Push off the bent leg to return to standing. Alternate sides or finish one side first.",
    ]),
    "Pause Squat": ("intermediate", [
        "Set up as for a back squat with the bar on your upper back.",
        "Squat down under control to your normal depth.",
        "Hold the bottom position for 2 to 3 seconds while staying tight; don't relax or bounce.",
        "Drive up out of the hole to standing.",
    ]),
    "Pendlay Row": ("intermediate", [
        "Stand with the bar over your midfoot, hinge until your back is flat and nearly parallel to the floor.",
        "Grip the bar slightly wider than shoulder-width.",
        "Row the bar explosively to your lower chest, keeping your torso still.",
        "Lower the bar all the way back to the floor and let it settle. Reset your back before the next rep.",
    ]),
    "Pike Push-up": ("intermediate", [
        "Start in a push-up position, then walk your feet forward and raise your hips into an upside-down V.",
        "Keep your arms and legs as straight as you can, hands about shoulder-width apart.",
        "Bend your elbows to lower the top of your head toward the floor between your hands.",
        "Press back up to the start. Raise your feet on a box to make it harder.",
    ]),
    "Push Press": ("intermediate", [
        "Hold the bar on your front shoulders with a grip just outside shoulder-width, elbows slightly in front of the bar.",
        "Dip a few inches by bending your knees, keeping your torso upright.",
        "Drive up hard through your legs and use that momentum to press the bar overhead.",
        "Lock your arms out with the bar over the back of your head, then lower it to your shoulders.",
    ]),
    "Seal Row": ("intermediate", [
        "Set a flat bench high enough (on boxes or plates) that your arms hang fully without the dumbbells touching the floor.",
        "Lie face down on the bench with a dumbbell in each hand, arms hanging straight.",
        "Row the dumbbells up toward your lower ribs, squeezing your shoulder blades together.",
        "Lower slowly to a full stretch. Keep your chest on the bench throughout.",
    ]),
    "Side Plank": ("beginner", [
        "Lie on your side with your elbow directly under your shoulder and your feet stacked or staggered.",
        "Lift your hips until your body forms a straight line from head to heels.",
        "Squeeze your glutes and brace your core; don't let your hips sag or rotate.",
        "Hold, then lower and switch sides. Bend your knees to make it easier.",
    ]),
    "Ski Erg": ("beginner", [
        "Stand facing the machine and grab one handle in each hand, arms extended overhead.",
        "Pull the handles down explosively, hinging at the hips and bending your knees slightly.",
        "Finish with your hands by your thighs and your core tight.",
        "Stand back up and let the handles rise slowly to the start before the next pull.",
    ]),
    "Split Squat": ("beginner", [
        "Hold a dumbbell in each hand and take a long step forward into a staggered stance.",
        "Keep your rear heel up and both feet flat on the floor (the back foot isn't elevated).",
        "Lower straight down until your rear knee nearly touches the floor.",
        "Drive through your front foot to stand back up. Finish the set and switch legs.",
    ]),
    "Swimming": ("beginner", [
        "Warm up with a few easy lengths in any stroke.",
        "Choose a stroke and a pace you can hold steadily for the whole session.",
        "Breathe out fully underwater and in smoothly when you turn or lift your head.",
        "Reach long with each stroke and keep your body flat and near the surface.",
    ]),
    "Toes-to-Bar": ("intermediate", [
        "Hang from a pull-up bar with an overhand grip, arms straight.",
        "Brace your core and press slightly against the bar to keep your shoulders engaged.",
        "Raise your legs, curling your hips up, until your toes touch the bar.",
        "Lower your legs under control without swinging into the next rep.",
    ]),
    "V-Up": ("intermediate", [
        "Lie on your back with your arms extended overhead and legs straight.",
        "Brace your core and lift your arms and legs at the same time.",
        "Reach your hands toward your feet so your body forms a V at the top.",
        "Lower your upper body and legs slowly back to the floor.",
    ]),
    "Wall Sit": ("beginner", [
        "Stand with your back against a wall, feet about two feet in front of you.",
        "Slide down the wall until your thighs are parallel to the floor and your knees are at 90 degrees.",
        "Keep your knees over your ankles and your back flat against the wall.",
        "Hold for the set time, then slide back up.",
    ]),
}
