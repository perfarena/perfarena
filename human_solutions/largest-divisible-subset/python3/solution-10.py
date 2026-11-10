def largest_divisible_subset(nums):
    nums.sort()
    size = 0
    result = []
    DP = [0] * len(nums)

    for i in range(len(nums)):
        current_size = 1
        current_result = [nums[i]]

        for j in range(i):
            if nums[i] % nums[j] == 0:
                if current_size < DP[j] + 1:
                    current_size = DP[j] + 1
                    current_result = result[j] + [nums[i]]

        DP[i] = current_size
        result.append(current_result)

        if current_size > size:
            size = current_size

    for res in result:
        if len(res) == size:
            return res

# Test the implementation
nums = [1, 2, 3, 4]
print(largest_divisible_subset(nums))