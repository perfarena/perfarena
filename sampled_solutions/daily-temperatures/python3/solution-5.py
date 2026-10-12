def dailyTemperatures(temperatures):
    stack = []
    result = [0] * len(temperatures)
    
    for i in range(len(temperatures)):
        while stack and temperatures[i] > temperatures[stack[-1]]:
            idx = stack.pop()
            result[idx] = i - idx
        stack.append(i)
    
    return result