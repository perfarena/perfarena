def findHighestAltitude(gain):
    maxHeight = 0
    currentHeight = 0
    
    for g in gain:
        currentHeight += g
        maxHeight = max(maxHeight, currentHeight)
    
    return maxHeight

# Example usage
gain = [-5, 1, 5, 0, -7]
print(findHighestAltitude(gain))  # Output: 1