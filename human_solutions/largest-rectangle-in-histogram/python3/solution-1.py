def largestRectangleArea(heights):
    max_area = 0
    stack = []
    
    for i, h in enumerate(heights):
        start_index = i
        
        while stack and h < stack[-1][1]:
            index, height = stack.pop()
            width = i - index
            max_area = max(max_area, height * width)
            start_index = index
        
        stack.append((start_index, h))
    
    for index, height in stack:
        width = len(heights) - index
        max_area = max(max_area, height * width)
    
    return max_area

# Example usage
histogram = [2, 1, 5, 6, 2, 3]
result = largestRectangleArea(histogram)
print(result)