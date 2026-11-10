def trap(height):
    n = len(height)
    if n < 3:
        return 0

    left, right = 0, n - 1
    max_left, max_right = height[0], height[n - 1]
    water = 0

    while left < right:
        if max_left <= max_right:
            left += 1
            max_left = max(max_left, height[left])
            water += max_left - height[left]
        else:
            right -= 1
            max_right = max(max_right, height[right])
            water += max_right - height[right]

    return water

# Test the function with the given elevation map
elevation_map = [0,1,0,2,1,0,1,3,2,1,2,1]
result = trap(elevation_map)
print(result)