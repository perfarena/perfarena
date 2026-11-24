for neighbor in graph[begin_word]:
    if distance[neighbor] == distance[begin_word] - 1:
        temp.append(neighbor)
        dfs(neighbor)
        temp.pop()

return result