# One of blue's tracks. Only what blue can measure: never the contact's orders or target.
string id
string cls           # contact class: jet, stealth, bomber, drone, missile, jammer, ...
int32 x
int32 y
int32 z
int32 vx             # movement over the last tick
int32 vy
int32 vz
float64 snr          # track quality
bool locked          # good enough for point defense to shoot at
float64 score        # threat model: chance it harms a blue unit within 5 ticks
int32 jam            # jam field radius if it is an emitter
string hit           # a blue unit it damaged in the last 3 ticks, if any
