def maxProduct(nums):
    answer = float('-inf')
    max_product = 1
    
    # Traverse from the start
    for i in range(len(nums)):
        max_product *= nums[i]
        answer = max(answer, max_product)
        if max_product == 0:
            max_product = 1
    
    max_product = 1
    
    # Traverse from the end
    for i in range(len(nums) - 1, -1, -1):
        max_product *= nums[i]
        answer = max(answer, max_product)
        if max_product == 0:
            max_product = 1
    
    return answer