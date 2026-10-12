def maxArea(height):
    max_area = 0
    i, j = 0, len(height) - 1
    
    while i < j:
        length = j - i
        breadth = min(height[i], height[j])
        area = length * breadth
        
        max_area = max(max_area, area)
        
        if height[i] < height[j]:
            i += 1
        else:
            j -= 1
    
    return max_area

# Test the function with input data
height = [1, 8, 6, 2, 5, 4, 8, 3, 7]
print(maxArea(height))  # Output: 49