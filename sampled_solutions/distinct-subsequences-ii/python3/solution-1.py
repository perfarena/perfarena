def sortedSquares(nums):
    result = [0] * len(nums)
    left, right = 0, len(nums) - 1

    for i in range(len(result) - 1, -1, -1):
        left_square = nums[left] ** 2
        right_square = nums[right] ** 2

        if left_square > right_square:
            result[i] = left_square
            left += 1
        else:
            result[i] = right_square
            right -= 1

    return result