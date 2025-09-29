// Minimal Qt-based virtualized gallery (Widgets/QListView IconMode)
// Build:
//   cmake -S . -B build -G "Ninja" -DCMAKE_PREFIX_PATH="<Qt6 dir>"
//   cmake --build build --config Release

#include <QtWidgets/QApplication>
#include <QtWidgets/QListView>
#include <QtCore/QDirIterator>
#include <QtGui/QIcon>
#include <QtGui/QStandardItemModel>

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    QString dir = (argc >= 2) ? QString::fromLocal8Bit(argv[1]) : QDir::currentPath();

    QStandardItemModel model;
    QStringList exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"};
    QDirIterator it(dir, QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) {
        QString p = it.next();
        QString lp = p.toLower();
        bool ok = false; for (const auto& e : exts) { if (lp.endsWith(e)) { ok = true; break; } }
        if (!ok) continue;
        QStandardItem* item = new QStandardItem(QIcon(p), QFileInfo(p).fileName());
        item->setData(p, Qt::UserRole+1);
        model.appendRow(item);
    }

    QListView view;
    view.setViewMode(QListView::IconMode);
    view.setResizeMode(QListView::Adjust);
    view.setUniformItemSizes(true);
    view.setIconSize(QSize(220, 124));
    view.setGridSize(QSize(240, 170));
    view.setModel(&model);
    view.setWindowTitle(QString("Qt Gallery: %1").arg(dir));
    view.resize(1200, 800);
    view.show();
    return app.exec();
}

