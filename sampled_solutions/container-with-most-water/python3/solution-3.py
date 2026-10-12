def max_area(heights):
    i, j = 0, len(heights) - 1
    max_area = 0
    
    while i < j:
        current_area = min(heights[i], heights[j]) * (j - i)
        max_area = max(max_area, current_area)
        
        if heights[i] < heights[j]:
            i += 1
        else:
            j -= 1
    
    return max_area