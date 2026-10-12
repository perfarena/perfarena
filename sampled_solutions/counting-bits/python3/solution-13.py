def countBits(n):
    answer = [0] * (n + 1)
    
    for i in range(1, n + 1):
        position = i // 2
        if i % 2 == 1:
            answer[i] = answer[position] + 1
        else:
            answer[i] = answer[position]
    
    return answer

# Test the function
n = 8
result = countBits(n)
print(result)