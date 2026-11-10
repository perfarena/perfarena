def sumNumbers(root):
    def calculateSum(node, currentSum):
        if not node:
            return None
        if not node.left and not node.right:
            return currentSum * 10 + node.val
        left_sum = calculateSum(node.left, currentSum * 10 + node.val) if node.left else 0
        right_sum = calculateSum(node.right, currentSum * 10 + node.val) if node.right else 0
        return left_sum + right_sum

    return calculateSum(root, 0)