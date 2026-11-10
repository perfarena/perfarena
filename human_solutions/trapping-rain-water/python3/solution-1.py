def trap(height):
    left, right = 0, len(height) - 1
    left_max = right_max = 0
    total_rainwater = 0

    while left < right:
        if height[left] <= height[right]:
            if height[left] >= left_max:
                left_max = height[left]
            else:
                total_rainwater += left_max - height[left]
            left += 1
        else:
            if height[right] >= right_max:
                right_max = height[right]
            else:
                total_rainwater += right_max - height[right]
            right -= 1

    return total_rainwater

# Test the function
height = [0,1,0,2,1,0,1,3,2,1,2,1]
print(trap(height))  # Output should be 6