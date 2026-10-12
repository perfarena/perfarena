def count_ones(n):
    answer = [0] * (n + 1)
    
    for i in range(n + 1):
        count = 0
        num = i
        
        while num != 0:
            count += num & 1
            num >>= 1
        
        answer[i] = count
    
    return answer

# Test the function with a sample input
n = 5
result = count_ones(n)
print(result)