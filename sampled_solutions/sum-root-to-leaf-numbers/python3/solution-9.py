def sum_root_to_leaf(root):
    total = 0

    def dfs(node, current):
        nonlocal total
        if not node:
            return 0
        current = current * 10 + node.val
        if not node.left and not node.right:
            total += current
        dfs(node.left, current)
        dfs(node.right, current)

    dfs(root, 0)
    return total