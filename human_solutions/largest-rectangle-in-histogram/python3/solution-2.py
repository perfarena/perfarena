def largest_rectangle_area(heights):
    stack = [-1]  # Initialize stack with -1
    max_area = 0

    for i in range(len(heights)):
        while stack[-1] != -1 and heights[i] <= heights[stack[-1]]:
            height = heights[stack.pop()]
            width = i - stack[-1] - 1
            max_area = max(max_area, height * width)
        
        stack.append(i)

    while stack[-1] != -1:
        height = heights[stack.pop()]
        width = len(heights) - stack[-1] - 1
        max_area = max(max_area, height * width)

    return max_area

# Example usage
histogram = [2, 1, 5, 6, 2, 3]
output = largest_rectangle_area(histogram)
print(output)  # Output: 10