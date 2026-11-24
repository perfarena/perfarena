def maxArea(height):
    maxArea = 0
    aPointer = 0
    bPointer = len(height) - 1
    
    while aPointer < bPointer:
        area = min(height[aPointer], height[bPointer]) * (bPointer - aPointer)
        maxArea = max(maxArea, area)
        
        if height[aPointer] < height[bPointer]:
            aPointer += 1
        else:
            bPointer -= 1
    
    return maxArea