current_altitude = 0
highest_altitude = 0

for gain in gains:
    current_altitude += gain
    highest_altitude = max(highest_altitude, current_altitude)

return highest_altitude