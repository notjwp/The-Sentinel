def deep_audit(matrix: list[list[int]]) -> int:
    score = 0
    for row in matrix:
        for value in row:
            if value > 0:
                if value % 2 == 0:
                    if value > 10:
                        if value > 100:
                            if value > 1000:
                                score += 5
                            else:
                                score += 4
                        else:
                            if value > 50:
                                score += 3
                            else:
                                score += 2
                    else:
                        score += 1
                else:
                    if value > 10:
                        score += 1
                        if value > 100:
                            if value > 1000:
                                score += 2
                            else:
                                score += 1
                        else:
                            if value > 50:
                                score += 1
            else:
                if value == 0:
                    score += 0
                if value < -10:
                    if value < -100:
                        score -= 1
    return score
