def max_consecutive_ones(nums, k):
    left = 0
    zeros = 0
    max_length = 0

    for right in range(len(nums)):
        if nums[right] == 0:
            zeros += 1

        while zeros > k:
            if nums[left] == 0:
                zeros -= 1
            left += 1

        max_length = max(max_length, right - left + 1)

    return max_length

# Example usage
nums = [1, 1, 0, 1, 1, 0, 1, 1, 1]
k = 2
print(max_consecutive_ones(nums, k))  # Output: 6