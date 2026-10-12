def find_min_position(start, end, heights):
    min_p = start
    for i in range(start, end):
        if heights[i] < heights[min_p]:
            min_p = i
    return min_p

def find_max_area(start, end, heights):
    if start > end:
        return 0
    
    min_p = find_min_position(start, end, heights)
    area_self = heights[min_p] * (end - start + 1)
    
    area_left = find_max_area(start, min_p - 1, heights)
    area_right = find_max_area(min_p + 1, end, heights)
    
    return max(area_self, area_left, area_right)

# Main function to find the maximum area in the histogram
def largest_rectangle_area(heights):
    return find_max_area(0, len(heights) - 1, heights)

# Example usage
histogram = [2, 1, 5, 6, 2, 3]
max_area = largest_rectangle_area(histogram)
print("Maximum area in the histogram:", max_area)