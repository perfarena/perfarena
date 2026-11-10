def largest_rectangle_area(heights):
    max_area = 0
    stack = []
    
    for i in range(len(heights)):
        while stack and heights[i] < heights[stack[-1]]:
            h = heights[stack.pop()]
            w = i if not stack else i - stack[-1] - 1
            max_area = max(max_area, h * w)
        
        stack.append(i)
    
    while stack:
        h = heights[stack.pop()]
        w = len(heights) if not stack else len(heights) - stack[-1] - 1
        max_area = max(max_area, h * w)
    
    return max_area

# Test the function with an example input
input_array = [2, 1, 5, 6, 2, 3]
print(largest_rectangle_area(input_array))  # Output should be 10