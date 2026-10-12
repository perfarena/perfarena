def find_indices(nums, target):
    previous_map = {}
    for i, n in enumerate(nums):
        diff = target - n
        if diff in previous_map:
            return [previous_map[diff], i]
        previous_map[n] = i
    return None  # No solution found

# Test the function
nums = [2, 1, 5, 3]
target = 9
result = find_indices(nums, target)
if result:
    print(result)  # Output: [0, 1] (indices of 2 and 7)
else:
    print("No solution found")