import pymupdf

def transform_point(matrix, x, y):
    point = pymupdf.Point(x,y) * pymupdf.Matrix(*matrix)
    return point.x, point.y

def inverse_transform(matrix):
    return tuple(~pymupdf.Matrix(*matrix))

def transformed_rect(matrix, rect):
    return tuple(pymupdf.Rect(rect) * pymupdf.Matrix(*matrix))

