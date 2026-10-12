function maxArea(heights):
    n = length of heights
    maxArea = 0
    i = 0
    j = n - 1
    
    while i < j:
        area = min(heights[i], heights[j]) * (j - i)
        maxArea = max(maxArea, area)
        
        if heights[i] < heights[j]:
            i++
        else:
            j--
    
    return maxArea