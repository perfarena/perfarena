# Initialize two iterators, fast and slow, at the head of the list
fast = slow = head

# Loop while fast is not a null pointer
while fast is not None:
    slow = slow.next  # Move slow one space
    fast = fast.next  # Move fast two spaces
    if fast is not None:
        fast = fast.next  # Move fast two spaces again
    else:
        return False  # No cycle if fast reaches the end

    # Check if fast and slow meet at the same node
    if fast == slow:
        return True  # Cycle detected

return False  # No cycle found