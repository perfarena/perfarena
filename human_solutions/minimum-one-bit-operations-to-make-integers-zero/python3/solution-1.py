def findHighestAltitude(gain):
    highestAltitude = 0
    currentAltitude = 0
    
    for g in gain:
        currentAltitude += g
        highestAltitude = max(highestAltitude, currentAltitude)
    
    return highestAltitude

# Test the function with the given example
gain = [-5, 1, 5, 0, -7]
result = findHighestAltitude(gain)
print(result)  # Output: 1