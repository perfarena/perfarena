def moveZeros(nums):
    zeros = 0  # Variable to keep track of the number of zeros
    for i in range(len(nums)):
        if nums[i] == 0:
            zeros += 1  # Increase zero counter
        else:
            nums[i - zeros] = nums[i]  # Swap non-zero element
    for i in range(len(nums) - zeros, len(nums)):
        nums[i] = 0  # Move zeros to the end
    return nums

# Test the function with input [0, 1, 0, 3, 12]
input_array = [0, 1, 0, 3, 12]
result = moveZeros(input_array)
print(result)