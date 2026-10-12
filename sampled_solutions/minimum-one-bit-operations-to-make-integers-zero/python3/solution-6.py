result = 0
altitude = 0
n = len(gain)

for i in range(n):
    altitude += gain[i]
    result = max(result, altitude)

return result