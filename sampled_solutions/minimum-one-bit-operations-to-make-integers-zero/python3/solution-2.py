n = len(gain)
altitude = [0] * (n + 1)

for i in range(n):
    altitude[i + 1] = altitude[i] + gain[i]

highest = max(altitude)
return highest