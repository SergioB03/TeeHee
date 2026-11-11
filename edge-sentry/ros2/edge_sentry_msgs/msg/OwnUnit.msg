# One of blue's own units, as it is this tick.
string id
string kind          # hq, sensor, relay, jet, stealth, ...
int32 x
int32 y
int32 z              # altitude level, 0 = ground
float64 hp
string action        # current order: none, move, altitude, orbit, launch, defend, ...
string target        # unit it is attacking or defending against, if any
int32 tx
int32 ty
int32 tz
